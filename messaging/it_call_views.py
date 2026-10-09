# messaging/it_call_views.py
"""The IT portal's Calls page: what calling — and file sending — is doing, live.

Two sources, one picture. The media bridge's /stats endpoint is the wire
truth — per-connection byte counters read from the WebRTC transport itself,
so the bandwidth shown is what the network actually carried (RTP, RTCP and
retransmits included), not an estimate from codec bitrates. The database is
the human truth — which call, whose, group or direct, how long, answered or
missed — and it is also what turns the bridge's anonymous peer ids back into
names, because the bridge knows nothing about users on purpose.

The same page carries the chat's file-transfer log: every file sent in a
conversation, who sent it, where, how big, with a link to open or download it
— files live under MEDIA_URL, so the link is the file. And it is where the
admin generates reports: CSV exports of calls and of file transfers over a
chosen date range, written with a UTF-8 BOM so Excel opens Arabic names
correctly rather than as mojibake.

Everything here is read-only and gated the same way as the rest of the IT
portal. The page polls its own data endpoint every few seconds; the bridge
being down is a state the page reports, not an error it fails on — mesh
calls still show from the database side, they just have no wire numbers,
because their audio never touches the server.
"""
import csv
import json
from datetime import datetime, timedelta

import requests as _requests
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from core.it_views import it_required

from . import call_config
from .bridge_common import bridge_max_participants, bridge_token, bridge_url
from .call_models import Call, CallPeer
from .models import Message, MessageFile


@it_required
def it_calls(request):
    return render(request, 'core/it/calls.html', {
        'tab': 'calls',
        'bridge_url': bridge_url(),
        'bridge_limit': bridge_max_participants(),
    })


def _bridge_stats():
    """The bridge's ledger, or None with a reason if it is not answering."""
    try:
        r = _requests.get(bridge_url() + '/stats',
                          headers={'X-Bridge-Token': bridge_token()},
                          timeout=2.0)
        data = r.json()
        if r.ok and data.get('ok'):
            return data, ''
        return None, data.get('error', f'HTTP {r.status_code}')
    except Exception as exc:
        return None, exc.__class__.__name__


def _names_for(rooms):
    """peer_id → who that is, plus each room's title and kind.

    Rooms are named `call-<pk>` by Django, so the ids inside them are
    CallPeer rows. One query for all rooms rather than one per room.
    """
    call_ids = []
    for room in rooms:
        name = str(room.get('room') or '')
        if name.startswith('call-') and name[5:].isdigit():
            call_ids.append(int(name[5:]))
    if not call_ids:
        return {}, {}, {}
    peers = (CallPeer.objects.filter(call_id__in=call_ids)
             .select_related('user'))
    names = {p.peer_id: (p.user.get_full_name() or p.user.username)
             for p in peers}
    calls = (Call.objects.filter(pk__in=call_ids)
             .select_related('conversation', 'started_by'))
    titles, groups = {}, {}
    for c in calls:
        conv = c.conversation
        titles[f'call-{c.pk}'] = ((conv.name or 'Group') if conv.is_group
                                  else conv.title_for(c.started_by))
        groups[f'call-{c.pk}'] = conv.is_group
    return names, titles, groups


def _outcome(call):
    """What happened to this call, in one word — the IT-side reading.

    Not per-person the way the phone log's is (a call is "missed" only for
    the person who missed it); the admin wants the call's own fate.
    """
    if call.status == Call.Status.LIVE:
        return 'ongoing'
    if call.ended_reason == Call.Ending.MISSED:
        return 'missed'
    if call.ended_reason == Call.Ending.DECLINED:
        return 'declined'
    return 'answered'


def _db_summary(user):
    """Calling as the database remembers it: live now, last day, last week —
    with group and direct calls counted apart, since a group call and a
    two-person call load the server very differently."""
    now = timezone.now()

    def bucket(since):
        qs = Call.objects.filter(started_at__gte=since)
        ended = qs.filter(status=Call.Status.ENDED)
        answered = ended.exclude(ended_reason__in=(Call.Ending.MISSED,
                                                   Call.Ending.DECLINED))
        minutes = sum(c.duration_seconds for c in answered) // 60
        return {
            'calls': qs.count(),
            'group': qs.filter(conversation__kind='group').count(),
            'direct': qs.filter(conversation__kind='direct').count(),
            'answered': answered.count(),
            'missed': ended.filter(ended_reason=Call.Ending.MISSED).count(),
            'declined': ended.filter(ended_reason=Call.Ending.DECLINED).count(),
            'minutes': minutes,
            'via_bridge': qs.filter(via_bridge=True).count(),
            'via_mesh': qs.filter(via_bridge=False).count(),
        }

    live = (Call.objects.filter(status=Call.Status.LIVE)
            .select_related('conversation', 'started_by')
            .order_by('-started_at')[:20])
    live_rows = []
    for c in live:
        peers = list(c.peers.filter(left_at__isnull=True)
                     .select_related('user'))
        conv = c.conversation
        live_rows.append({
            'id': c.pk,
            'title': (conv.name or 'Group') if conv.is_group
                     else conv.title_for(c.started_by),
            'is_group': conv.is_group,
            'via_bridge': c.via_bridge,
            'started_by': c.started_by.get_full_name() or c.started_by.username,
            'seconds': c.duration_seconds,
            'people': [p.user.get_full_name() or p.user.username
                       for p in peers],
        })
    return {
        'live': live_rows,
        'day': bucket(now - timedelta(hours=24)),
        'week': bucket(now - timedelta(days=7)),
    }


# ── the file-transfer log ────────────────────────────────────────────────

#: How many transfers the page shows. A window, not an archive — the report
#: export below is the archive.
FILES_SHOWN = 40


def _file_entry(fieldfile, message):
    """One transfer, however it was stored (see Message.all_files)."""
    name = fieldfile.name.rsplit('/', 1)[-1] if fieldfile else ''
    try:
        size = fieldfile.size
    except Exception:
        size = 0            # the row outlived the file on disk
    conv = message.conversation
    return {
        'sent_at': message.sent_at.isoformat(),
        'sender': message.sender.get_full_name() or message.sender.username,
        'conv': (conv.name or 'Group') if conv.is_group
                else str(conv.pk),
        'is_group': conv.is_group,
        'conv_id': conv.pk,
        'name': name,
        'bytes': size,
        'url': fieldfile.url if fieldfile else '',
    }


def _direct_title(entries):
    """Direct chats read as "A ↔ B" rather than a conversation number.

    Done in one pass over the participants of every direct conversation on
    the page, instead of a query per row.
    """
    from .models import Conversation
    ids = {e['conv_id'] for e in entries if not e['is_group']}
    if not ids:
        return
    convs = (Conversation.objects.filter(pk__in=ids)
             .prefetch_related('participants'))
    titles = {}
    for c in convs:
        people = [u.get_full_name() or u.username
                  for u in c.participants.all()[:2]]
        titles[c.pk] = ' ↔ '.join(people) if people else f'Chat {c.pk}'
    for e in entries:
        if not e['is_group']:
            e['conv'] = titles.get(e['conv_id'], e['conv'])


def _recent_files(limit=FILES_SHOWN):
    """The last `limit` files sent in chat, both storages, newest first."""
    entries = []
    new = (MessageFile.objects
           .select_related('message__sender', 'message__conversation')
           .order_by('-pk')[:limit])
    entries += [_file_entry(f.file, f.message) for f in new]
    old = (Message.objects.exclude(attachment='')
           .exclude(attachment__isnull=True)
           .select_related('sender', 'conversation')
           .order_by('-pk')[:limit])
    entries += [_file_entry(m.attachment, m) for m in old]
    entries.sort(key=lambda e: e['sent_at'], reverse=True)
    entries = entries[:limit]
    _direct_title(entries)
    return entries


def _file_counts():
    now = timezone.now()

    def bucket(since):
        return (MessageFile.objects.filter(message__sent_at__gte=since).count()
                + Message.objects.exclude(attachment='')
                  .exclude(attachment__isnull=True)
                  .filter(sent_at__gte=since).count())

    return {'day': bucket(now - timedelta(hours=24)),
            'week': bucket(now - timedelta(days=7))}


@it_required
def it_calls_data(request):
    stats, why_down = _bridge_stats()
    payload = {
        'ok': True,
        'bridge_up': stats is not None,
        'bridge_down_reason': why_down,
        'bridge_url': bridge_url(),
        'db': _db_summary(request.user),
        'files': _recent_files(),
        'file_counts': _file_counts(),
    }
    if stats is not None:
        names, titles, groups = _names_for(stats.get('rooms', []))
        for room in stats.get('rooms', []):
            room['title'] = titles.get(room.get('room'), room.get('room'))
            room['is_group'] = groups.get(room.get('room'), False)
            for peer in room.get('peers', []):
                peer['name'] = names.get(peer.get('peer'), peer.get('peer'))
            owner = room.get('sharing_owner') or ''
            room['sharing_name'] = names.get(owner, '') if owner else ''
        payload['bridge'] = {
            'uptime_s': stats.get('uptime_s', 0),
            'stats_interval_s': stats.get('stats_interval_s', 10),
            'rooms': stats.get('rooms', []),
            'history': list(reversed(stats.get('history', []))),
            'totals': stats.get('totals', {}),
        }
    return JsonResponse(payload)


# ── reports ──────────────────────────────────────────────────────────────

def _report_range(request):
    """The dates the admin asked for, inclusive, defaulting to the last 30
    days. Bad input falls back rather than erroring: a report with the
    default range beats a form argument. Aware or naive follows USE_TZ —
    this project runs naive (Windows/MSSQL), and mixing the two is an
    exception, not a report."""
    now = timezone.now()

    def parse(name, fallback):
        raw = (request.GET.get(name) or '').strip()
        try:
            dt = datetime.strptime(raw, '%Y-%m-%d')
        except ValueError:
            return fallback
        return (timezone.make_aware(dt, timezone.get_current_timezone())
                if settings.USE_TZ else dt)

    since = parse('from', now - timedelta(days=30))
    until = parse('to', now)
    # `to` is a date the admin means to include, so the cut is its midnight
    # after — and a range given backwards is swapped, not refused.
    until = until + timedelta(days=1)
    if since >= until:
        since, until = until - timedelta(days=1), since + timedelta(days=1)
    return since, until


def _csv_response(filename, header, rows):
    """A CSV Excel opens correctly: UTF-8 with a BOM, so Arabic names and
    the ↔ in chat titles survive the double-click."""
    resp = HttpResponse(content_type='text/csv; charset=utf-8')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    resp.write('\ufeff')
    writer = csv.writer(resp)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return resp


@it_required
def it_calls_report(request):
    """The export button: calls or file transfers, over a date range, as CSV.

    Streamed from the database, not from the bridge — the bridge's memory is
    a monitoring window that dies with the process, while the report is the
    archive an admin files. Bandwidth therefore appears in the live page and
    the bridge history, not in the export, which reports what the database
    can stand behind for any date.
    """
    since, until = _report_range(request)
    stamp = f"{since.strftime('%Y-%m-%d')}_{(until - timedelta(days=1)).strftime('%Y-%m-%d')}"

    def local(dt):
        """A timestamp for the CSV, in server time, aware or not."""
        if dt is None:
            return ''
        if timezone.is_aware(dt):
            dt = timezone.localtime(dt)
        return dt.strftime('%Y-%m-%d %H:%M:%S')

    if request.GET.get('what') == 'files':
        rows = []
        new = (MessageFile.objects
               .filter(message__sent_at__gte=since, message__sent_at__lt=until)
               .select_related('message__sender', 'message__conversation')
               .order_by('message__sent_at'))
        old = (Message.objects.exclude(attachment='')
               .exclude(attachment__isnull=True)
               .filter(sent_at__gte=since, sent_at__lt=until)
               .select_related('sender', 'conversation')
               .order_by('sent_at'))
        entries = ([_file_entry(f.file, f.message) for f in new]
                   + [_file_entry(m.attachment, m) for m in old])
        entries.sort(key=lambda e: e['sent_at'])
        _direct_title(entries)
        for e in entries:
            rows.append([e['sent_at'].replace('T', ' ')[:19], e['sender'],
                         'group' if e['is_group'] else 'direct',
                         e['conv'], e['name'], e['bytes'],
                         request.build_absolute_uri(e['url'])])
        return _csv_response(
            f'aji-hub-file-transfers_{stamp}.csv',
            ['Sent at', 'Sender', 'Chat type', 'Conversation', 'File',
             'Size (bytes)', 'Link'],
            rows)

    calls = (Call.objects
             .filter(started_at__gte=since, started_at__lt=until)
             .select_related('conversation', 'started_by')
             .prefetch_related('peers__user')
             .order_by('started_at'))
    rows = []
    for c in calls:
        conv = c.conversation
        seen, people = set(), []
        for p in c.peers.all():
            if p.user_id in seen:
                continue                    # two tabs are one person
            seen.add(p.user_id)
            people.append(p.user.get_full_name() or p.user.username)
        outcome = _outcome(c)
        rows.append([
            c.pk,
            local(c.started_at),
            local(c.ended_at),
            c.duration_seconds if outcome in ('answered', 'ongoing') else 0,
            'group' if conv.is_group else 'direct',
            (conv.name or 'Group') if conv.is_group else conv.title_for(c.started_by),
            c.started_by.get_full_name() or c.started_by.username,
            '; '.join(people),
            len(people),
            outcome,
            'bridge' if c.via_bridge else 'mesh',
        ])
    return _csv_response(
        f'aji-hub-calls_{stamp}.csv',
        ['Call ID', 'Started at', 'Ended at', 'Talk time (s)', 'Type',
         'Conversation', 'Started by', 'Participants', 'People', 'Outcome',
         'Carried by'],
        rows)


# ── the call settings page ───────────────────────────────────────────────

def _clamp(raw, lo, hi, fallback):
    try:
        return max(lo, min(hi, int(str(raw).strip())))
    except (TypeError, ValueError):
        return fallback


def _flag(request, name):
    return request.POST.get(name) == 'on'


@it_required
def it_call_settings(request):
    """Every knob calling has, on one form — codecs, Opus tuning, screen
    caps, routing limits, ICE. Values live in the database (call_config), so
    saving applies to the next call joined with no deploy; the two the bridge
    reads at startup are marked on the page. Reset deletes the row, which is
    a clean return to settings.py behaviour.
    """
    error = ''
    if request.method == 'POST':
        if request.POST.get('reset'):
            call_config.reset(request.user)
            return redirect(f"{reverse('it_call_settings')}?saved=1")
        data = call_config.effective()
        r, a, s, t = (data['routing'], data['audio'],
                      data['screen'], data['transport'])

        r['carry_p2p'] = _flag(request, 'carry_p2p')
        r['bridge_max_participants'] = _clamp(
            request.POST.get('bridge_max'), 2, 128,
            r['bridge_max_participants'])
        r['mesh_max_participants'] = _clamp(
            request.POST.get('mesh_max'), 2, 8, r['mesh_max_participants'])

        codec = request.POST.get('audio_codec', a['codec'])
        a['codec'] = codec if codec in call_config.AUDIO_CODECS else a['codec']
        # Opus takes 6–510 kbps by spec; 0 is "let the browser decide".
        a['opus_max_kbps'] = _clamp(request.POST.get('opus_max_kbps'),
                                    0, 510, a['opus_max_kbps'])
        if 0 < a['opus_max_kbps'] < 6:
            a['opus_max_kbps'] = 6
        a['opus_fec'] = _flag(request, 'opus_fec')
        a['opus_dtx'] = _flag(request, 'opus_dtx')
        a['echo_cancellation'] = _flag(request, 'echo_cancellation')
        a['noise_suppression'] = _flag(request, 'noise_suppression')
        a['auto_gain'] = _flag(request, 'auto_gain')

        codec = request.POST.get('screen_codec', s['codec'])
        s['codec'] = codec if codec in call_config.SCREEN_CODECS else s['codec']
        s['max_kbps'] = _clamp(request.POST.get('screen_max_kbps'),
                               0, 8000, s['max_kbps'])
        s['capture_fps'] = _clamp(request.POST.get('capture_fps'),
                                  1, 30, s['capture_fps'])
        s['capture_width'] = _clamp(request.POST.get('capture_width'),
                                    320, 3840, s['capture_width'])
        s['bridge_max_width'] = _clamp(request.POST.get('bridge_max_width'),
                                       320, 3840, s['bridge_max_width'])
        s['bridge_fps'] = _clamp(request.POST.get('bridge_fps'),
                                 1, 30, s['bridge_fps'])

        policy = request.POST.get('ice_policy', t['ice_policy'])
        t['ice_policy'] = (policy if policy in call_config.ICE_POLICIES
                           else t['ice_policy'])
        t['advertise_ip'] = (request.POST.get('advertise_ip') or '').strip()
        raw = (request.POST.get('ice_servers') or '').strip()
        if not raw:
            t['ice_servers'] = []
        else:
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, list):
                    raise ValueError
                t['ice_servers'] = parsed
            except ValueError:
                error = ('ICE servers must be a JSON list, e.g. '
                         '[{"urls": "stun:turn.ajigroup.com:3478"}] — '
                         'nothing was saved.')
        if not error:
            call_config.save(data, request.user)
            return redirect(f"{reverse('it_call_settings')}?saved=1")

    cfg = call_config.effective()
    return render(request, 'core/it/call_settings.html', {
        'tab': 'calls',
        'cfg': cfg,
        'error': error,
        'saved': request.GET.get('saved') == '1',
        'audio_codecs': call_config.AUDIO_CODECS,
        'screen_codecs': call_config.SCREEN_CODECS,
        'ice_servers_json': json.dumps(cfg['transport']['ice_servers'],
                                       indent=2)
                            if cfg['transport']['ice_servers'] else '',
    })
