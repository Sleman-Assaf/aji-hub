# messaging/call_views.py
"""The call API: a switchboard, not a bridge.

Every endpoint here does one of three things — put somebody into a call, take
somebody out of one, or carry a note from one browser to another. The audio and
the shared screen never touch Django; browsers send those to each other directly
once this has introduced them.

`sync` is the whole client-facing protocol in one request. A browser in a call
asks it about once a second and gets back three things at once: the roster, the
signalling addressed to it, and whether the call is still going on. That it was
asked at all is what proves the browser is still there, so the same request is
also the heartbeat — a separate keep-alive would be a second request per second
per person to say something this one already knows.

Two rules hold throughout:

* Nothing is taken on the client's word. Which call, which peer, and whether
  either belongs to you are all re-derived from the database on every request —
  a `peer` in a form field is a claim, not an identity.
* Every read tidies up. There is no scheduler on the Hub, so the sweeps that end
  abandoned calls and drop vanished browsers run inside the requests that would
  notice them anyway.
"""
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (Call, CallInvite, CallPeer, CallSignal, Conversation,
                     MAX_PARTICIPANTS, MAX_SIGNAL_BYTES, RING_SECONDS)

#: STUN and TURN servers, as RTCPeerConnection wants them. Empty by default,
#: which is right for one office: two machines on the same network find each
#: other with the addresses their own network cards report, and adding a public
#: STUN server would send AJi's internal addressing to somebody else's service
#: for no benefit. Somebody calling in over the VPN or from home is the case that
#: needs entries here — see the note in settings.py.
def ice_servers():
    from .call_config import effective
    return effective()['transport'].get('ice_servers') or []


# ── the media bridge ─────────────────────────────────────────────────────
# Group calls are carried by the media bridge when it is running (see
# messaging/media_bridge.py), which is what lifts them past the 8-person mesh
# limit. Whether it is running is asked, not configured: a health check,
# cached for a few seconds so that joining a call costs at most one quick
# local HTTP round trip and usually none.

import logging
import time as _time

import requests as _requests

from .bridge_common import (bridge_carries_p2p, bridge_max_participants,
                            bridge_token, bridge_url)
from .call_config import client_rtc as _client_rtc

#: The signalling trace. Everything a call does through Django — starting,
#: answering, declining, leaving, mode decisions, mute and share changes,
#: notes carried between browsers, offers proxied to the bridge — is logged
#: here, so the Waitress terminal shows the whole story of a call next to the
#: bridge terminal showing its media. Wired to the console in settings.LOGGING.
clog = logging.getLogger('calls')

_BRIDGE_HEALTH = {'ok': False, 'at': 0.0}
_BRIDGE_TTL = 5.0


def bridge_up():
    now = _time.monotonic()
    if now - _BRIDGE_HEALTH['at'] < _BRIDGE_TTL:
        return _BRIDGE_HEALTH['ok']
    ok = False
    try:
        r = _requests.get(bridge_url() + '/health',
                          headers={'X-Bridge-Token': bridge_token()},
                          timeout=0.7)
        ok = r.ok and r.json().get('ok') is True
    except Exception as exc:
        ok = False
        if _BRIDGE_HEALTH['ok']:
            clog.warning('media bridge went DOWN (%s) — new calls fall back '
                         'to the browser mesh', exc.__class__.__name__)
    if ok and not _BRIDGE_HEALTH['ok']:
        clog.info('media bridge is UP at %s — new calls will route '
                  'through it', bridge_url())
    _BRIDGE_HEALTH.update(ok=ok, at=now)
    return ok


def _bridge_post(path, payload):
    """Tell the bridge something it can live without hearing.

    Everything Django sends the bridge outside of the offer exchange is a
    hint — who holds the stage, who left — that the bridge would work out
    itself from the media going quiet. So a bridge that is slow or gone gets
    a short timeout and no complaint.
    """
    try:
        _requests.post(bridge_url() + path, json=payload,
                       headers={'X-Bridge-Token': bridge_token()},
                       timeout=1.0)
    except Exception:
        pass


def call_limit(call):
    """How many people this call takes, which depends on what carries it.
    Both limits come from the admin's call settings; the mesh one is capped
    at MAX_PARTICIPANTS because past eight the mesh's uplink cost is a fact
    of arithmetic, not an opinion an admin can override."""
    from .call_config import effective
    if call.via_bridge:
        return bridge_max_participants()
    return min(int(effective()['routing'].get('mesh_max_participants',
                                              MAX_PARTICIPANTS)),
               MAX_PARTICIPANTS)


# ── helpers ──────────────────────────────────────────────────────────────

def _conv(request, pk):
    """A conversation this user is actually in, or 404."""
    return get_object_or_404(Conversation, pk=pk, participants=request.user)


def _call(request, pk):
    """A call this user belongs to, or 404 — in the conversation, or holding
    an invitation into this specific call (see CallInvite)."""
    return get_object_or_404(
        Call.objects.filter(
            Q(conversation__participants=request.user)
            | Q(invites__user=request.user)).distinct(), pk=pk)


def _my_peer(request, call):
    """The browser session this request claims to be, if it really is mine.

    Checked against the user as well as the call: a peer id is short and is
    handed to everybody in the roster, so on its own it is a name, not a key.
    """
    peer_id = (request.POST.get('peer') or request.GET.get('peer') or '').strip()
    if not peer_id:
        return None
    return call.peers.filter(peer_id=peer_id, user=request.user).first()


def _person(user):
    from .views import _avatar, _initials
    name = user.get_full_name() or user.username
    return {'id': user.pk, 'name': name, 'initials': _initials(name),
            'photo': _avatar(user)}


def _peer_json(peer, me_peer_id=''):
    return dict(_person(peer.user),
                peer_id=peer.peer_id,
                muted=peer.muted,
                sharing=peer.sharing,
                is_me=peer.peer_id == me_peer_id)


def _ringing_people(call):
    """Whoever the call is still ringing for, so the caller can see it doing so.

    Two clocks: conversation participants ring for RING_SECONDS from the
    call's start, and anyone invited mid-call rings for RING_SECONDS from
    their own invitation — a participant with an invitation follows the
    invitation, which is how re-inviting somebody makes them ring again.
    """
    if call.status != Call.Status.LIVE:
        return []
    now = timezone.now()
    window = timedelta(seconds=RING_SECONDS)
    joined = call.joined_user_ids()
    declined = set(call.declined_by.values_list('pk', flat=True))
    invites = {i.user_id: i for i in
               call.invites.select_related('user', 'user__employee')}
    out = []
    if now - call.started_at <= window:
        for u in call.conversation.participants.select_related('employee'):
            if u.pk in joined or u.pk in declined or u.pk in invites:
                continue
            out.append(_person(u))
    for invite in invites.values():
        if invite.user_id in joined or invite.user_id in declined:
            continue
        if now - invite.created_at > window:
            continue
        out.append(_person(invite.user))
    return out


def _call_json(call, me_peer_id='', peers=None):
    peers = list(peers if peers is not None else call.active_peers())
    sharing = next((p.peer_id for p in peers if p.sharing), '')
    return {
        'id': call.pk,
        'conv_id': call.conversation_id,
        'title': call.conversation.title_for(call.started_by)
                 if not call.conversation.is_group else (call.conversation.name or 'Group'),
        'is_group': call.conversation.is_group,
        'status': call.status,
        'started_at': call.started_at.isoformat(),
        'started_by': _person(call.started_by),
        'peers': [_peer_json(p, me_peer_id) for p in peers],
        'ringing': _ringing_people(call),
        'sharing_peer': sharing,
        'via_bridge': call.via_bridge,
        'max_participants': call_limit(call),
    }


def live_call(conv):
    """The call going on in this conversation, if there is one.

    Ends it here if it has run out of people: the last browser to hold it open
    may have been closed rather than hung up, and this is the read that notices.
    """
    call = Call.objects.filter(conversation=conv, status=Call.Status.LIVE).first()
    if call is None:
        return None
    if not _still_alive(call):
        return None
    return call


def _still_alive(call):
    """True if the call has anybody in it — ending it if it does not.

    Two ways a call runs out. Everybody's browser has gone quiet, which
    `active_peers` works out from the heartbeat. Or nobody ever picked up: the
    caller is alone, the ringing window has passed, and it is a missed call —
    which has to be written down, because a missed call people never heard about
    is the one thing a phone must not do.
    """
    peers = list(call.active_peers())
    if not peers:
        call.end(Call.Ending.HUNG_UP if call.peers.count() > 1
                 else Call.Ending.MISSED)
        return False
    # A call that ever had a second browser in it was answered, whoever that
    # browser belonged to. Asking instead for somebody other than the caller
    # looked right and was not: one person who rings from their desk and picks
    # up on their laptop is two peers talking to each other, and the sweep hung
    # up on them at forty-five seconds and wrote it down as a missed call.
    answered = call.peers.count() > 1
    rang_out = timezone.now() - call.started_at > timedelta(seconds=RING_SECONDS)
    if not answered and rang_out:
        call.end(Call.Ending.MISSED)
        return False
    # A direct call is two people, so one of them leaving ends it — there is
    # nobody left to talk to. A group call is the opposite: people come and go
    # and the last one may well be waiting for somebody, so it stands until the
    # room is empty, which is the case above. A direct call somebody was added
    # into counts as a group call (is_group_call), or adding a third person
    # would end the call the moment either original party stepped out.
    if answered and not call.is_group_call and len(peers) < 2:
        call.end(Call.Ending.HUNG_UP)
        return False
    return True


# ── joining and leaving ──────────────────────────────────────────────────

@login_required
@require_POST
def api_call_join(request, pk):
    """Start a call in this conversation, or answer the one already going on.

    One endpoint for both, because from the server's side they are the same
    request — put this browser into this conversation's live call — and the
    caller is simply the person who arrives when there is nothing to join.
    """
    conv = (Conversation.objects.filter(pk=pk)
            .filter(Q(participants=request.user)
                    | Q(calls__invites__user=request.user,
                        calls__status=Call.Status.LIVE))
            .distinct().first())
    if conv is None:
        return JsonResponse({'error': 'That call has ended.'}, status=404)
    call = live_call(conv)
    starting = call is None

    if starting:
        # An invitation is a ticket into a live call, not membership: once
        # that call is over, somebody who was invited into it cannot start a
        # new one in a conversation they are not part of.
        if not conv.participants.filter(pk=request.user.pk).exists():
            return JsonResponse({'error': 'That call has ended.'}, status=404)
        if conv.participants.count() < 2:
            return JsonResponse({'error': 'There is nobody here to call.'}, status=400)
        # EVERY call goes through the media bridge when it is running — group
        # calls because it lifts the mesh's 8-person limit, direct calls
        # because the bridge is the one path that works wherever the Hub
        # does (VLANs, Wi-Fi isolation, VPN — see bridge_carries_p2p).
        # Decided here and stored, not re-asked later: everybody in one call
        # must be in it the same way. The browser mesh remains only as the
        # fallback for when the bridge process is not running, or when
        # MEDIA_BRIDGE["carry_p2p"] is switched off for direct calls.
        up = bridge_up()
        use_bridge = up and (conv.is_group or bridge_carries_p2p())
        call = Call.objects.create(conversation=conv, started_by=request.user,
                                   via_bridge=use_bridge)
        clog.info('call %d STARTED by %s in conv %d (%s) — carried by %s%s',
                  call.pk, request.user.username, conv.pk,
                  'group' if conv.is_group else 'direct',
                  'the media bridge' if use_bridge else 'a browser mesh',
                  '' if up else ' (bridge is down)')
        # Two people pressing call in the same second would otherwise each start
        # one and sit in separate calls wondering why the other cannot be heard.
        # Whichever was written first is the call; the other is abandoned before
        # anybody has joined it, so it leaves no trace in the conversation.
        first = (Call.objects.filter(conversation=conv, status=Call.Status.LIVE)
                 .order_by('pk').first())
        if first is not None and first.pk != call.pk:
            Call.objects.filter(pk=call.pk).delete()
            call = first
            starting = False
    else:
        # Answering is also how you stop your own other devices ringing.
        call.declined_by.remove(request.user)

    peers = list(call.active_peers())
    limit = call_limit(call)
    if len(peers) >= limit:
        return JsonResponse(
            {'error': f'This call is full — {limit} people is the limit.'},
            status=409)

    peer = call.add_peer(request.user)
    conv.touch()
    clog.info('call %d: %s joined as peer %s (%d in call, %s, limit %d)',
              call.pk, request.user.username, peer.peer_id,
              len(peers) + 1, 'bridge' if call.via_bridge else 'mesh', limit)
    return JsonResponse({
        'ok': True,
        'started': starting,
        'peer_id': peer.peer_id,
        'ice_servers': ice_servers(),
        'rtc': _client_rtc(),
        'bridge': call.via_bridge,
        'call': _call_json(call, peer.peer_id, peers=peers + [peer]),
    })


@login_required
@require_POST
def api_call_decline(request, pk):
    """No. Stops it ringing on my screens and tells the caller.

    Declining a group call is only about my own phone — everybody else's keeps
    ringing, and the call goes ahead without me. A direct call has nobody else to
    ring, so the last no ends it.
    """
    call = _call(request, pk)
    call.declined_by.add(request.user)
    clog.info('call %d: %s DECLINED', call.pk, request.user.username)
    if call.status == Call.Status.LIVE and not _ringing_people(call) \
            and not call.peers.exclude(user=call.started_by).exists():
        call.end(Call.Ending.DECLINED)
    return JsonResponse({'ok': True, 'status': call.status})


@login_required
@require_POST
def api_call_leave(request, pk):
    """Hang up. The call carries on unless I was the last one in it."""
    call = _call(request, pk)
    peer = _my_peer(request, call)
    if peer is not None:
        peer.leave()
        clog.info('call %d: %s hung up (peer %s)', call.pk,
                  request.user.username, peer.peer_id)
        # Everyone else finds out from their own sync — but a goodbye lets them
        # tear the connection down at once rather than waiting out the heartbeat,
        # which is the difference between a call that ends and one that fades.
        for other in call.active_peers():
            if other.peer_id != peer.peer_id:
                CallSignal.objects.create(call=call, from_peer=peer.peer_id,
                                          to_peer=other.peer_id,
                                          kind=CallSignal.Kind.BYE)
    # Asked rather than worked out here, so that leaving and a browser simply
    # going quiet reach the same conclusion — including the one about a direct
    # call being over the moment either side hangs up.
    ended = not _still_alive(call)
    if call.via_bridge:
        if ended:
            _bridge_post('/close', {'room': f'call-{call.pk}'})
        elif peer is not None:
            _bridge_post('/leave', {'room': f'call-{call.pk}',
                                    'peer': peer.peer_id})
    return JsonResponse({'ok': True, 'ended': ended})


@login_required
@require_POST
def api_call_bridge_offer(request, pk):
    """Carry one browser's offer to the media bridge and its answer back.

    The one part of a bridge call's signalling that is not a hint. Proxied
    through Django rather than exposing the bridge to browsers, so that who
    may connect stays exactly the question the rest of the call API answers —
    a member of the conversation, in the call, being the peer they claim.
    """
    call = _call(request, pk)
    peer = _my_peer(request, call)
    if peer is None or peer.left_at is not None:
        return JsonResponse({'error': 'You are not in this call.'}, status=403)
    if not call.via_bridge or call.status != Call.Status.LIVE:
        return JsonResponse({'error': 'This call is not on the bridge.'},
                            status=409)
    sdp = request.POST.get('sdp') or ''
    if not sdp or len(sdp) > MAX_SIGNAL_BYTES:
        clog.warning('call %d: bad bridge offer from %s (%d bytes)',
                     call.pk, request.user.username, len(sdp))
        return JsonResponse({'error': 'Bad offer.'}, status=400)
    clog.info('call %d: proxying offer from %s (peer %s, %d bytes) to the '
              'bridge', call.pk, request.user.username, peer.peer_id, len(sdp))
    _t0 = _time.monotonic()
    try:
        r = _requests.post(bridge_url() + '/offer',
                           json={'room': f'call-{call.pk}',
                                 'peer': peer.peer_id, 'sdp': sdp},
                           headers={'X-Bridge-Token': bridge_token()},
                           timeout=15)
        data = r.json()
    except Exception as exc:
        clog.error('call %d: the media bridge did not answer the offer for '
                   'peer %s: %s', call.pk, peer.peer_id, exc)
        return JsonResponse({'error': 'The media server did not answer. '
                                      'Tell IT the bridge is down.'},
                            status=502)
    if not r.ok or not data.get('sdp'):
        clog.error('call %d: bridge refused the offer for peer %s: %s',
                   call.pk, peer.peer_id, data.get('error', r.status_code))
        return JsonResponse({'error': data.get('error', 'Bridge refused.')},
                            status=502)
    clog.info('call %d: bridge answered peer %s in %d ms', call.pk,
              peer.peer_id, int((_time.monotonic() - _t0) * 1000))
    return JsonResponse({'ok': True, 'sdp': data['sdp']})


# ── being in a call ──────────────────────────────────────────────────────

@login_required
@require_POST
def api_call_state(request, pk):
    """Mute, unmute, start or stop sharing a screen.

    The microphone and the screen are switched off in the browser regardless —
    this is what tells everybody else, so the roster can draw a crossed-out
    microphone rather than leaving people wondering why somebody is silent.
    """
    call = _call(request, pk)
    peer = _my_peer(request, call)
    if peer is None:
        return JsonResponse({'error': 'You are not in this call.'}, status=403)

    fields = []
    if 'muted' in request.POST:
        peer.muted = request.POST.get('muted') == '1'
        fields.append('muted')
    if 'sharing' in request.POST:
        peer.sharing = request.POST.get('sharing') == '1'
        fields.append('sharing')
    if fields:
        peer.save(update_fields=fields)
        clog.info('call %d: %s (peer %s) %s', call.pk, request.user.username,
                  peer.peer_id,
                  ', '.join(f'{f}={getattr(peer, f)}' for f in fields))
    # One screen at a time. Everybody in a mesh call sends their screen to
    # everybody else, so two sharers is twice the traffic for every person in
    # the call and half of it is being ignored. Starting takes it over, the way
    # it does in Teams; whoever had it sees `sharing` go false on their own row
    # in the next sync and stops.
    if peer.sharing:
        call.peers.filter(sharing=True).exclude(pk=peer.pk).update(sharing=False)
    # The bridge draws the stage, so it is told who holds it. A hint, not a
    # dependency: it also notices whose frames are flowing.
    if call.via_bridge and 'sharing' in request.POST:
        _bridge_post('/state', {'room': f'call-{call.pk}',
                                'peer': peer.peer_id,
                                'sharing': peer.sharing})
    return JsonResponse({'ok': True, 'muted': peer.muted, 'sharing': peer.sharing})


@login_required
@require_POST
def api_call_signal(request, pk):
    """Leave a note for another browser in this call.

    Django does not know or care what is in it. An offer, an answer and a
    candidate are all the browser's own words, handed over untouched — parsing
    SDP here would be inventing a second implementation of WebRTC to no purpose.
    """
    call = _call(request, pk)
    peer = _my_peer(request, call)
    if peer is None or peer.left_at is not None:
        return JsonResponse({'error': 'You are not in this call.'}, status=403)

    kind = request.POST.get('kind') or ''
    if kind not in CallSignal.Kind.values:
        return JsonResponse({'error': 'Unknown signal.'}, status=400)
    payload = request.POST.get('payload') or ''
    if len(payload) > MAX_SIGNAL_BYTES:
        return JsonResponse({'error': 'Signal too large.'}, status=400)

    to = (request.POST.get('to') or '').strip()
    # Addressed to somebody who is actually here. Without this, signalling would
    # accept anything as a destination and the table would take writes from a
    # participant aimed at nobody.
    if not call.peers.filter(peer_id=to, left_at__isnull=True).exists():
        return JsonResponse({'error': 'That peer has left.'}, status=409)

    CallSignal.objects.create(call=call, from_peer=peer.peer_id, to_peer=to,
                              kind=kind, payload=payload)
    clog.debug('call %d: signal %s %s -> %s (%d bytes)', call.pk, kind,
               peer.peer_id, to, len(payload))
    return JsonResponse({'ok': True})


@login_required
def api_call_sync(request, pk):
    """The roster, my post, and whether this is still happening.

    Called about once a second by everybody in a call and by nobody else, which
    is what keeps a one-second cadence affordable: the four-second poll that
    watches for messages runs for every signed-in person, this one runs only for
    the handful who are actually on a call.
    """
    call = _call(request, pk)
    peer = _my_peer(request, call)
    if peer is None:
        return JsonResponse({'error': 'You are not in this call.'}, status=403)
    if peer.left_at is not None:
        # Ending a call marks everybody in it as gone, so the person who did not
        # hang up arrives here too — and they are the one who wants to be told
        # how long it lasted rather than merely that it is over.
        return JsonResponse({
            'ended': True, 'status': call.status,
            'summary': (call.summary() if call.status == Call.Status.ENDED
                        else 'You left this call.'),
        })

    # Before anything is read: I am here. Otherwise a long sync could time me out
    # on the strength of the request I am making right now.
    peer.ping()

    if call.status != Call.Status.LIVE or not _still_alive(call):
        call.refresh_from_db()
        return JsonResponse({'ended': True, 'status': call.status,
                             'summary': call.summary()})

    try:
        after = int(request.GET.get('after', 0))
    except (TypeError, ValueError):
        after = 0
    notes = list(call.signals.filter(to_peer=peer.peer_id, pk__gt=after)[:100])

    peer.refresh_from_db()
    return JsonResponse({
        'ended': False,
        'call': _call_json(call, peer.peer_id),
        'me': {'muted': peer.muted, 'sharing': peer.sharing},
        'signals': [{'id': s.pk, 'from': s.from_peer, 'kind': s.kind,
                     'payload': s.payload} for s in notes],
    })


# ── the log ──────────────────────────────────────────────────────────────

#: How far back the log goes in one request. Enough that nobody scrolls to the
#: end of it in normal use, and bounded because this is one query per row's worth
#: of prefetching and a phone log is not an archive.
LOG_LIMIT = 80

#: The outcomes that count as a call nobody had. Named here because the filter on
#: the screen and the query behind it have to agree — deciding "missed" twice, in
#: two places, is how a Missed tab ends up showing something that is not.
MISSED_OUTCOMES = ('missed', 'no_answer')


def call_row(call, user, peers=None):
    """One line of the log, from this person's side.

    The same call reads differently to the two people on it, and the log has to
    say what happened *to you*: a call you did not pick up is missed, and to the
    person who rang it is unanswered. A call you declined is neither — you saw it
    and said no, and showing that as "missed" would be telling you that you
    missed something you did not.
    """
    peers = list(peers if peers is not None else call.peers.all())
    conv = call.conversation
    mine = call.started_by_id == user.pk
    joined = any(p.user_id == user.pk for p in peers)
    declined = any(u.pk == user.pk for u in call.declined_by.all())

    if call.status == Call.Status.LIVE:
        outcome = 'ongoing'
    elif mine:
        # Nobody but me was ever in it, whatever the recorded reason: a call that
        # rang out and one that was declined both come back to the caller as
        # nobody having answered, and `ended_reason` distinguishes them.
        outcome = ('declined' if call.ended_reason == Call.Ending.DECLINED
                   else 'no_answer' if not joined_by_anyone_else(peers, user)
                   else 'answered')
    elif joined:
        outcome = 'answered'
    else:
        outcome = 'declined' if declined else 'missed'

    # Who was on it. The roster, not the conversation's membership — a group of
    # nine where three people talked is a call between those three.
    from .views import _initials
    others = [p.user for p in peers if p.user_id != user.pk]
    seen, took_part = set(), []
    for u in others:
        if u.pk in seen:
            continue                       # somebody with two tabs is one person
        seen.add(u.pk)
        took_part.append(_person(u))

    with_name = conv.title_for(user)
    return {
        'id': call.pk,
        'conv_id': conv.pk,
        'is_group': conv.is_group,
        'with_name': with_name,
        'initials': _initials(with_name),
        'photo': (conv.photo.url if (conv.is_group and conv.photo)
                  else (took_part[0]['photo'] if took_part else '')),
        'direction': 'out' if mine else 'in',
        'outcome': outcome,
        'missed': outcome in MISSED_OUTCOMES,
        'started_at': call.started_at.isoformat(),
        # Zero on a call nobody answered — the forty-five seconds it spent
        # ringing is not a length of call, and showing it as one would suggest
        # somebody was on the line.
        'seconds': call.duration_seconds if outcome in ('answered', 'ongoing') else 0,
        'started_by': _person(call.started_by),
        'people': took_part,
    }


def joined_by_anyone_else(peers, user):
    """Did a browser other than this person's ever join? See call_row."""
    return any(p.user_id != user.pk for p in peers)


def missed_since(user, after_id):
    """How many calls this person missed that they have not looked at yet.

    "Not looked at" is the id of the newest call in the log the last time they
    opened it, which the browser remembers and sends back. Kept out of the
    database on purpose: a per-user marker would be a column, a migration and a
    write on every glance at the log, to hold something that only matters to the
    browser showing the badge — and if it is lost, the badge is briefly wrong
    about a phone call rather than wrong about anything that matters.

    Only calls newer than the marker are examined, so this is nothing almost
    every time it runs.
    """
    if after_id is None:
        return 0
    fresh = (Call.objects
             .filter(conversation__participants=user, pk__gt=after_id,
                     status=Call.Status.ENDED)
             .exclude(started_by=user)          # my own unanswered calls are not
             .prefetch_related('declined_by',   # something I missed
                               Prefetch('peers', queryset=CallPeer.objects
                                        .select_related('user')))
             .order_by('-pk')[:LOG_LIMIT])
    return sum(1 for c in fresh
               if call_row(c, user, peers=list(c.peers.all()))['missed'])


@login_required
def api_call_log(request):
    """Every call in this person's conversations, most recent first.

    Answered from `Call` rather than from the "Voice call · 3 min" lines in the
    chat: those are prose written for a conversation, and a log needs the parts
    separately — who, which way, how long, and whether anybody picked up. Reading
    them back out of a sentence would also make the log wrong in the one case it
    most needs to be right, because that line is the same sentence for both
    people and a missed call is only missed for one of them.
    """
    conv_ids = list(request.user.conversations.values_list('pk', flat=True))
    calls = (Call.objects
             .filter(conversation_id__in=conv_ids)
             .select_related('conversation', 'started_by', 'started_by__employee')
             .prefetch_related('declined_by',
                               Prefetch('peers', queryset=CallPeer.objects
                                        .select_related('user', 'user__employee')))
             .order_by('-started_at'))

    only = request.GET.get('conv')
    if str(only).isdigit() and int(only) in conv_ids:
        calls = calls.filter(conversation_id=int(only))

    everything = [call_row(c, request.user, peers=list(c.peers.all()))
                  for c in calls[:LOG_LIMIT]]
    # Before the filter. Looking at the Missed tab is still looking at the log,
    # so the marker has to cover the calls the reader could have seen by
    # switching tabs — otherwise the badge would keep counting a call that is one
    # click away on screen.
    newest = max((r['id'] for r in everything), default=0)

    rows = ([r for r in everything if r['missed']]
            if request.GET.get('missed') == '1' else everything)
    return JsonResponse({
        'calls': rows,
        'missed_total': sum(1 for r in everything if r['missed']),
        # "I have seen up to here", which the browser keeps and sends back on the
        # poll so the badge counts only what has happened since.
        'newest_id': newest,
    })


# ── the ring, for the chat's own poll ────────────────────────────────────

def ring_for(user):
    """The call ringing for this person right now, or None.

    Read by the chat's ordinary four-second poll rather than by a timer of its
    own: a second recurring request for every signed-in person, running all day
    to say "no" almost every time, is a lot of load for a phone that rings twice
    a week. The cost of hearing about a call is therefore one extra query on a
    request that was being made anyway.
    """
    calls = (Call.objects.filter(status=Call.Status.LIVE)
             .filter(Q(conversation__participants=user) | Q(invites__user=user))
             .exclude(started_by=user).distinct()
             .select_related('conversation', 'started_by', 'started_by__employee')
             .order_by('-started_at')[:4])
    for call in calls:
        # Sweeps up calls whose caller closed the tab, which is why this runs
        # over the ringing ones rather than testing them in the query.
        if not _still_alive(call):
            continue
        if not call.is_ringing_for(user):
            continue
        conv = call.conversation
        # Somebody invited mid-call rings on their own clock and sees the
        # name of whoever added them, not of whoever started the call an
        # hour ago in a chat they may not even be part of.
        invite = (call.invites.filter(user=user)
                  .select_related('invited_by', 'invited_by__employee')
                  .order_by('-created_at').first())
        ring_from = (invite.invited_by if invite and invite.invited_by
                     else call.started_by)
        return {
            'call_id': call.pk,
            'conv_id': conv.pk,
            'is_group': conv.is_group or call.is_group_call,
            'where': (conv.name or 'Group') if conv.is_group else '',
            'from': _person(ring_from),
            'started_at': (invite.created_at if invite
                           else call.started_at).isoformat(),
            'rings_for': RING_SECONDS,
        }
    return None


# ── growing a call ───────────────────────────────────────────────────────

@login_required
@require_POST
def api_call_invite(request, pk):
    """Add somebody into this live call — any call, any time.

    Anyone already on the call can do it, more than once: a direct call
    becomes a group call with the first addition (see Call.is_group_call),
    and a group call simply grows. The invited person's phone rings for the
    full window from this moment, wherever in the Hub they are, and the
    invitation is their ticket into the call even when the conversation
    underneath is not theirs. Re-inviting somebody who missed or declined
    rings them again.
    """
    call = _call(request, pk)
    peer = _my_peer(request, call)
    if peer is None or peer.left_at is not None:
        return JsonResponse({'error': 'You are not in this call.'}, status=403)
    if call.status != Call.Status.LIVE:
        return JsonResponse({'error': 'That call has ended.'}, status=400)
    try:
        target = User.objects.get(pk=int(request.POST.get('user') or 0),
                                  is_active=True)
    except (ValueError, User.DoesNotExist):
        return JsonResponse({'error': 'No such person.'}, status=404)
    if target.pk == request.user.pk:
        return JsonResponse({'error': 'You are already here.'}, status=400)
    if call.peers.filter(user=target, left_at__isnull=True).exists():
        return JsonResponse({'error': f'{target.get_full_name() or target.username} '
                                      'is already on the call.'}, status=400)
    # Room for them: the people connected plus the phones already ringing.
    seats = call.active_peers().count() + len(_ringing_people(call))
    if seats >= call_limit(call):
        return JsonResponse({'error': 'The call is full.'}, status=400)

    grew = (not call.conversation.is_group
            and not call.invites.exists())      # about to become a group call
    invite, created = CallInvite.objects.get_or_create(
        call=call, user=target, defaults={'invited_by': request.user})
    if not created:
        # A fresh window and a fresh name on the ring card.
        invite.invited_by = request.user
        invite.created_at = timezone.now()
        invite.save(update_fields=['invited_by', 'created_at'])
    # Declining is an answer to one ringing, not a standing instruction.
    call.declined_by.remove(target)
    clog.info('call %d: %s INVITED %s (%s; %d on call)', call.pk,
              request.user.username, target.username,
              'direct call becomes a group call' if grew
              else 're-ring' if not created else 'group grows',
              call.active_peers().count())
    # Nothing to tell the bridge: the new person connects like anybody else.
    return JsonResponse({'ok': True,
                         'call': _call_json(call, me_peer_id=peer.peer_id)})


@login_required
def api_call_invitees(request, pk):
    """Who could be added: a name search over the Hub, minus whoever is
    already here. Small on purpose — eight rows is a picker, not a
    directory."""
    call = _call(request, pk)
    peer = _my_peer(request, call)
    if peer is None or peer.left_at is not None:
        return JsonResponse({'error': 'You are not in this call.'}, status=403)
    q = (request.GET.get('q') or '').strip()
    on_call = set(call.peers.filter(left_at__isnull=True)
                  .values_list('user_id', flat=True))
    ringing = {p['id'] for p in _ringing_people(call)}
    users = (User.objects.filter(is_active=True)
             .exclude(pk__in=on_call).exclude(pk=request.user.pk))
    if q:
        users = users.filter(Q(first_name__icontains=q)
                             | Q(last_name__icontains=q)
                             | Q(username__icontains=q))
    users = users.select_related('employee').order_by('first_name',
                                                      'last_name')[:8]
    return JsonResponse({'ok': True, 'people': [
        dict(_person(u), ringing=u.pk in ringing) for u in users]})
