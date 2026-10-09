# messaging/views.py
"""Chat: one API, two surfaces.

The popup in the corner and the full page at /messaging/ used to be different
code — the popup was a live JSON client, the page was server-rendered with no
polling, no attachments and its own markup. Two implementations of the same
screen meant every fix had to be made twice, and one of them was always behind.

So everything below is JSON, and both surfaces render from it. The full page is
the same engine in a bigger shell.

Rules that hold everywhere here:

* You can only touch a conversation you are in. Every view re-derives that from
  the object rather than trusting a hidden field.
* Deleting a chat is *for you*. It hides your view of it and nothing else, so
  one person can never remove somebody else's copy of a conversation.
* Only the author edits a message, and never a system note.
* A group has one owner — its creator, or an Intranet Admin.
"""
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Count, Max, Prefetch, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.people import person_json, search_people
from .models import (EDIT_WINDOW, MAX_ATTACHMENT_BYTES, MAX_ATTACHMENTS,
                     MAX_MESSAGE_BYTES, QUICK_REACTIONS, ChatState,
                     Conversation, Message, MessageFile, Reaction)


# ── helpers ──────────────────────────────────────────────────────────────

def _mine(request, pk):
    """A conversation this user is actually in, or 404."""
    return get_object_or_404(Conversation, pk=pk, participants=request.user)


def _avatar(user):
    emp = getattr(user, 'employee', None)
    return emp.photo.url if (emp is not None and emp.photo) else ''


def _online(user):
    emp = getattr(user, 'employee', None)
    try:
        return bool(emp and emp.is_online())
    except Exception:
        return False


#: The four things somebody can be, in the order they override each other.
#: Being on a call beats being merely present, and sharing a screen beats being
#: on a call — because those are the two states where interrupting somebody
#: costs them something, and they are the whole reason for showing this at all.
PRESENCE_LABELS = {
    'online': 'Active now',
    'busy': 'In a call',
    'presenting': 'Presenting',
    'offline': 'Offline',
}


def presence_for(users):
    """{user_id: 'online' | 'busy' | 'presenting' | 'offline'}

    One query for the calls, whatever the size of the list — a row per person
    would be a query per row on a conversation list that redraws every few
    seconds. Last-seen is already on the employee record each user carries.
    """
    from .models import Call, CallPeer, PEER_TIMEOUT

    users = list(users)
    state = {u.pk: ('online' if _online(u) else 'offline') for u in users}
    if not users:
        return state
    live = (CallPeer.objects
            .filter(user_id__in=[u.pk for u in users], left_at__isnull=True,
                    call__status=Call.Status.LIVE,
                    last_ping__gte=timezone.now() - PEER_TIMEOUT)
            .values_list('user_id', 'sharing'))
    for uid, sharing in live:
        # Sharing wins if any of this person's browsers is doing it.
        if sharing or state.get(uid) != 'presenting':
            state[uid] = 'presenting' if sharing else 'busy'
    return state


#: How many people one request may ask about. The picker shows 20 and a busy
#: conversation list is a handful, so this is well clear of both.
PRESENCE_WATCH_MAX = 60


def presence_payload(ids):
    """{user_id: {'presence': …, 'label': …}} for the ids the browser is showing.

    The browser says which people are on screen and this answers for exactly
    those, so presence costs the same whether somebody has two chats open or
    twenty rows of search results. Capped, because the ids arrive in a query
    string: a caller cannot turn one poll into a thousand-row lookup.

    Ids that are not real users simply do not come back, rather than erroring —
    a stale row on a page that has been open a while is not a bad request.
    """
    ids = [i for i in ids if i][:PRESENCE_WATCH_MAX]
    if not ids:
        return {}
    users = User.objects.filter(pk__in=ids).select_related('employee')
    return {uid: {'presence': state, 'label': PRESENCE_LABELS.get(state, '')}
            for uid, state in presence_for(users).items()}


def _watched_ids(raw):
    """The `watch=1,2,3` parameter, as ids. Anything unparseable is dropped."""
    out = []
    for part in (raw or '').split(','):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def _initials(name):
    parts = [p for p in (name or '').split() if p]
    if len(parts) >= 2:
        return (parts[0][0] + parts[-1][0]).upper()
    return (parts[0][0].upper() if parts else '?')


_UNSET = object()


def _conv_json(conv, user, state=None, last=_UNSET, unread=None, presence=None):
    """One row in the conversation list, from this person's side.

    `last` is passed in by the list, which fetches every conversation's newest
    message in one query — asking each conversation for its own would be a
    query per row, and prefetching them all would load every message in every
    chat to show one line of preview.
    """
    state = state or conv.state_for(user)
    others = [p for p in conv.participants.all() if p.pk != user.pk]

    other_id = None
    if conv.is_group:
        title = conv.name or 'Group'
        photo = conv.photo.url if conv.photo else ''
        online = False
        standing = ''
        # What the group is for, when somebody has said — it describes the group
        # better than a list of the first four members ever could. The names are
        # the fallback for the groups that have no description.
        subtitle = conv.description or (
            ', '.join((p.get_full_name() or p.username).split()[0]
                      for p in others[:4]) or 'Just you')
        if not conv.description and len(others) > 4:
            subtitle += f' +{len(others) - 4}'
    else:
        other = others[0] if others else user
        other_id = other.pk
        title = other.get_full_name() or other.username
        photo = _avatar(other)
        # The list works out everybody's at once; a lone row asks for its own.
        standing = (presence or presence_for([other])).get(other.pk, 'offline')
        online = standing != 'offline'
        emp = getattr(other, 'employee', None)
        subtitle = getattr(emp, 'role', '') or ''

    if last is _UNSET:
        last = conv.last_message(user, state)
    preview, last_icon = '', ''
    if last:
        who = ''
        if conv.is_group and not last.system:
            who = ('You: ' if last.sender_id == user.pk
                   else (last.sender.get_full_name() or last.sender.username).split()[0] + ': ')
        body = last.text
        if not body:
            files = last.all_files()
            body = last.attachment_label()
            # An icon name for the browser to draw, rather than an emoji baked
            # into the string. 📎 and 🖼 render as whatever the machine happens
            # to have — usually something a decade older than everything around
            # it — and cannot be styled, sized or coloured with the rest.
            if files:
                last_icon = 'image' if all(f.is_image for f in files) else 'paperclip'
        preview = ('' if last.system else who) + body

    return {
        'id': conv.pk,
        'kind': conv.kind,
        'is_group': conv.is_group,
        'with_name': title,
        'subtitle': subtitle,
        # Raw, for the box that edits it — `subtitle` above has already fallen
        # back to a list of names, which must never be typed into that field.
        'description': conv.description if conv.is_group else '',
        'initials': _initials(title),
        'photo': photo,
        'is_online': online,
        'presence': standing,
        'presence_label': PRESENCE_LABELS.get(standing, ''),
        # Whose presence this row shows, so the browser can ask the poll to keep
        # it up to date. Without it a row could only be refreshed by fetching the
        # whole conversation list, which is why a green dot could outlive the
        # person going offline by however long that gap was.
        'other_id': other_id,
        'last_icon': last_icon,
        'members': len(others) + 1,
        'pinned': state.pinned,
        # The list passes this in, having counted every chat in one query.
        # Working it out here as well meant a COUNT per conversation on every
        # refresh — which is what made opening the chat feel slow.
        'unread': conv.unread_count(user, state) if unread is None else unread,
        'last_message': preview,
        'last_ts': last.sent_at.isoformat() if last else None,
        'last_is_me': bool(last and last.sender_id == user.pk),
        'is_admin': conv.is_admin(user),
    }


def _msg_json(m, user, seen_before=None):
    """One bubble. `seen_before` is the slowest reader's mark in this chat."""
    sender = m.sender
    files = m.all_files()
    reply = None
    if m.reply_to_id and m.reply_to is not None:
        r = m.reply_to
        reply = {
            'id': r.pk,
            'sender': (r.sender.get_full_name() or r.sender.username),
            'text': (r.text or (f'📎 {r.filename}' if r.filename else ''))[:140],
            'is_image': r.is_image,
        }
    return {
        'id': m.pk,
        'text': m.text,
        'is_me': m.sender_id == user.pk,
        'system': m.system,
        'kind': m.kind,
        'sender': sender.get_full_name() or sender.username,
        'sender_id': m.sender_id,
        'initials': _initials(sender.get_full_name() or sender.username),
        'photo': _avatar(sender),
        'sent_at': m.sent_at.isoformat(),
        'edited_at': m.edited_at.isoformat() if m.edited_at else None,
        # Every file on the message. The three keys under it describe the first
        # one and are kept because the reply quote and the conversation-list
        # preview both want "the attachment" singular — a reply to a message
        # with four photos still quotes it as one thing.
        'files': [{'url': f.file.url, 'name': f.filename,
                   'is_image': f.is_image, 'size': f.filesize}
                  for f in files],
        'file': files[0].file.url if files else '',
        'filename': files[0].filename if files else '',
        'is_image': files[0].is_image if files else False,
        'filesize': files[0].filesize if files else '',
        'reply': reply,
        'reactions': m.reaction_summary(user),
        'seen': bool(seen_before and seen_before >= m.sent_at),
        'can_edit': m.can_edit(user),
        # When the Edit button should go away. Sent as a deadline rather than as
        # "seconds left" so that it stays true in a tab left open — a countdown
        # would be wrong by however long the page has been sitting there — and so
        # the browser needs nothing from the server to retire the button.
        # Only on your own messages: nobody else's is editable at any point.
        'edit_until': (m.editable_until().isoformat()
                       if (m.sender_id == user.pk and not m.system) else None),
    }


def _unread_map(user, conv_ids, states):
    """{conversation_id: unread} for every chat at once.

    One query. It used to be a COUNT per conversation, run on every refresh of
    the list — which the poll triggers whenever anything arrives — so somebody in
    a dozen chats paid a dozen queries several times a minute, and the panel felt
    like it was thinking. Each person's cut-off differs, so the thresholds go into
    the WHERE clause as a chain of ORs rather than into separate queries.
    """
    if not conv_ids:
        return {}
    clause = Q(pk__in=[])
    for cid in conv_ids:
        state = states.get(cid)
        one = Q(conversation_id=cid)
        if state and state.cleared_at:
            one &= Q(sent_at__gt=state.cleared_at)
        if state and state.last_read_at:
            one &= Q(sent_at__gt=state.last_read_at)
        clause |= one
    rows = (Message.objects.filter(clause)
            .exclude(sender=user)
            .exclude(hidden_for=user)
            .values('conversation_id')
            .annotate(n=Count('pk')))
    return {r['conversation_id']: r['n'] for r in rows}


def _my_conversations(user):
    """My chats: pinned first, then newest.

    Four queries in total, whatever the number of conversations: the rows, their
    newest message, my state for each, and one grouped unread count.
    """
    convs = list(user.conversations
                 .prefetch_related(Prefetch(
                     'participants', queryset=User.objects.select_related('employee')))
                 .annotate(last_id=Max('messages__id'))
                 .order_by('-updated_at'))

    # `files` too: the preview line asks each newest message what it is carrying,
    # and without it that is one query per conversation in the list.
    lasts = {m.pk: m for m in Message.objects
             .filter(pk__in=[c.last_id for c in convs if c.last_id])
             .select_related('sender').prefetch_related('files')}
    states = {s.conversation_id: s for s in ChatState.objects.filter(user=user)}
    unread = _unread_map(user, [c.pk for c in convs], states)
    # Everybody in every direct chat, in one go — a row at a time would be a
    # query per conversation on a list that redraws whenever anything arrives.
    others = {p for c in convs if not c.is_group
              for p in c.participants.all() if p.pk != user.pk}
    standing = presence_for(others)

    rows = []
    for conv in convs:
        state = states.get(conv.pk) or conv.state_for(user)
        last = lasts.get(conv.last_id)
        # Everything before cleared_at is hidden from me, and the newest message
        # is the latest there is — so if that one is behind the mark, there is
        # nothing of this conversation left for me to see.
        if last is not None and state.cleared_at and last.sent_at <= state.cleared_at:
            last = None

        # A cleared chat stays off my list until somebody says something new.
        # The moment it matters again, it comes back on its own.
        if state.hidden:
            if last is None:
                continue
            state.hidden = False
            state.save(update_fields=['hidden'])

        rows.append(_conv_json(conv, user, state, last=last,
                               unread=unread.get(conv.pk, 0), presence=standing))

    # Stable sort, so recency survives inside each group.
    rows.sort(key=lambda r: not r['pinned'])
    return rows


def _system(conv, actor, text):
    Message.objects.create(conversation=conv, sender=actor, text=text, system=True)


def _clean_emoji(raw):
    """An emoji, or None. Deliberately narrow: this string is rendered.

    Rejecting anything with ASCII in it means no markup, no protocol, no
    entity — a reaction cannot become a way to put HTML in somebody's chat.
    """
    e = (raw or '').strip()
    if not e or len(e) > 16:
        return None
    if any(ord(c) < 128 for c in e):
        return None
    return e


# ── pages ────────────────────────────────────────────────────────────────

@login_required
def messaging_home(request):
    """The full-page chat. The popup is the same engine in a smaller shell."""
    open_id = request.GET.get('c') or ''
    return render(request, 'messaging/messaging.html', {
        'open_id': open_id if str(open_id).isdigit() else '',
        'start_new': bool(request.GET.get('new')),
        'quick_reactions': QUICK_REACTIONS,
        'max_mb': MAX_ATTACHMENT_BYTES // 1048576,
    })


@login_required
def conversation_detail(request, pk):
    """Kept because links to it exist — a shared Exchange thread sends one.

    It opens the full page with that conversation showing rather than being a
    third rendering of the same screen.
    """
    conv = _mine(request, pk)
    return redirect(f"/messaging/?c={conv.pk}")


@login_required
def get_or_create_conversation(request, user_pk):
    conv = _direct_with(request.user, get_object_or_404(User, pk=user_pk))
    return redirect(f"/messaging/?c={conv.pk}")


def _direct_with(me, other):
    """The one-to-one chat between two people, made if it does not exist.

    Two things this has to get right, and both are easy to get wrong.

    It must be filtered to direct conversations: once groups exist, "the
    conversation containing both of us" also matches every group we are both in,
    and messaging a colleague would drop the message into a group instead.

    And the size check has to be done per row. Chaining two `filter()` calls on
    the same many-to-many builds two joins, so `Count('participants')` counts
    join rows rather than people — it returned 2 for a three-person group and
    made a second chat with the same person each time.
    """
    both = (Conversation.objects
            .filter(kind=Conversation.Kind.DIRECT, participants=me)
            .filter(participants=other)
            .distinct()
            .prefetch_related('participants'))
    for conv in both:
        if conv.participants.count() == 2:
            return conv
    conv = Conversation.objects.create(kind=Conversation.Kind.DIRECT)
    conv.participants.add(me, other)
    return conv


# ── the list ─────────────────────────────────────────────────────────────

@login_required
def api_conversations(request):
    return JsonResponse({'conversations': _my_conversations(request.user)})


@login_required
def api_unread(request):
    """Just the badge. Building the whole list to add up one number was three
    queries and a payload nobody read."""
    states = {s.conversation_id: s for s in ChatState.objects.filter(user=request.user)}
    ids = list(request.user.conversations.values_list('pk', flat=True))
    return JsonResponse({'unread': sum(_unread_map(request.user, ids, states).values())})


# ── one conversation ─────────────────────────────────────────────────────

@login_required
def api_messages(request, pk):
    conv = _mine(request, pk)
    state = conv.state_for(request.user)

    msgs = list(conv.visible_messages(request.user, state)
                .select_related('sender', 'sender__employee',
                                'reply_to', 'reply_to__sender')
                .prefetch_related('reactions', 'files'))
    seen_before = conv.slowest_read_mark(request.user)

    # Marked read *after* building the payload, so the ticks in this response
    # describe the moment the reader arrived rather than a state they caused.
    state.mark_read()

    others = [p for p in conv.participants.all() if p.pk != request.user.pk]
    standing = presence_for(others)
    return JsonResponse({
        'conversation': _conv_json(conv, request.user, state, presence=standing),
        # Yourself, so the browser can pick your own name out of a message and
        # mark it. Being mentioned is the one mention that matters to you, and
        # the participants list below deliberately does not include you.
        'me': person_json(request.user),
        'participants': [dict(person_json(p),
                              online=standing.get(p.pk, 'offline') != 'offline',
                              presence=standing.get(p.pk, 'offline'),
                              presence_label=PRESENCE_LABELS.get(
                                  standing.get(p.pk, 'offline'), ''))
                         for p in others],
        'messages': [_msg_json(m, request.user, seen_before) for m in msgs],
    })


@login_required
@require_POST
def api_send(request, pk):
    conv = _mine(request, pk)
    text = (request.POST.get('text') or '').strip()
    uploads = request.FILES.getlist('attachment')

    # A message needs either words or a file — an empty one is not a message.
    if not text and not uploads:
        return JsonResponse({'error': 'text or attachment required'}, status=400)
    if len(uploads) > MAX_ATTACHMENTS:
        return JsonResponse(
            {'error': f'Up to {MAX_ATTACHMENTS} files at a time.'}, status=400)
    mb = MAX_ATTACHMENT_BYTES // 1048576
    for up in uploads:
        if up.size > MAX_ATTACHMENT_BYTES:
            return JsonResponse(
                {'error': f'“{up.name}” is larger than the {mb} MB limit.'},
                status=400)
    # Each file is under the limit and the whole message still has to be: ten
    # files just under the cap is a quarter of a gigabyte in one send.
    if sum(u.size for u in uploads) > MAX_MESSAGE_BYTES:
        return JsonResponse(
            {'error': f'That is more than the {MAX_MESSAGE_BYTES // 1048576} MB '
                      f'a single message can carry.'}, status=400)

    reply_to = None
    raw = (request.POST.get('reply_to') or '').strip()
    if raw.isdigit():
        # Only a message from this same conversation: a reply that quotes
        # somewhere else would leak it into a chat it does not belong to.
        reply_to = Message.objects.filter(pk=raw, conversation=conv).first()

    # Only `birthday` may be set by a caller. The other kinds mark system notes
    # the server writes itself, and a client able to claim "this is a missed
    # call" could put a call into a transcript that never happened.
    kind = 'birthday' if request.POST.get('kind') == 'birthday' else ''

    msg = Message.objects.create(conversation=conv, sender=request.user,
                                 text=text, reply_to=reply_to, kind=kind)
    # All of them onto the new table, including a lone one. Splitting by count
    # would mean two storage paths to think about for the rest of the file's
    # life; `attachment` is left to the messages that already use it.
    # One at a time rather than bulk_create: writing the file to storage is a
    # side effect of saving the row, and that is not a thing to be clever about.
    for i, up in enumerate(uploads):
        MessageFile.objects.create(message=msg, file=up, position=i)
    conv.touch()
    # Sending is reading: my own message must not come back as unread to me.
    conv.state_for(request.user).mark_read()
    _tell_the_mentioned(conv, msg, request.user)
    _email_the_absent(conv, msg, request)
    return JsonResponse({'message': _msg_json(msg, request.user)})


def _tell_the_mentioned(conv, msg, author):
    """Ring the bell for anybody named in this message — and nobody else.

    Mentions are written as @username, the same as everywhere else on the Hub,
    so core.mentions does the parsing and core.notify decides how each person
    hears about it.

    The one rule this adds is the important one: **only people in the
    conversation**. `extract_mentioned_users` searches everybody at AJi, and a
    notification carries a preview of what was said — so telling somebody
    outside the chat that they were named would hand them a line of a private
    conversation they are not part of. Typing @somebody who is not here does
    nothing at all, which is also what the picker offers.
    """
    if not msg.text:
        return
    from core.mentions import extract_mentioned_users
    from core.notify import Event, notify

    members = {u.pk: u for u in conv.participants.all()}
    named = [u for u in extract_mentioned_users(msg.text, exclude=author)
             if u.pk in members]
    if not named:
        return
    who = author.get_full_name() or author.username
    where = conv.name if conv.is_group else 'your chat'
    for user in named:
        try:
            notify(user, Event.MENTION, f'{who} mentioned you in {where}',
                   body=msg.text[:300], link=f'/messaging/?c={conv.pk}',
                   actor=author,
                   case='chat.mentioned',
                   values={'actor': who, 'message': msg.text[:300],
                           'where': where,
                           'name': user.get_full_name() or user.username})
        except Exception:                                      # noqa: BLE001
            # Sending a message must never fail because a mail server did.
            pass


def _email_the_absent(conv, msg, request):
    """Email a chat message to anyone who has asked for that and is not here.

    Two guards, both about not being a nuisance. It is off by default — the 5pm
    digest is the sane way to hear about chat by email. And even when it is on,
    somebody currently using the Hub does not need an email about a message they
    are about to see; the pop-up in their browser already told them.
    """
    from core.notify import Event, notify

    others = [p for p in conv.participants.all()
              if p.pk != request.user.pk and (p.email or '').strip()]
    if not others:
        return
    who = request.user.get_full_name() or request.user.username
    where = f' in {conv.name}' if conv.is_group else ''
    label = msg.attachment_label()
    preview = (msg.text or (f'Sent {label}' if label else ''))[:300]
    link = f'/messaging/?c={conv.pk}'
    for user in others:
        if _online(user):
            continue
        try:
            notify(user, Event.CHAT, f'{who} messaged you{where}',
                   body=preview, link=link, actor=request.user,
                   case='chat.message_while_away',
                   values={'actor': who, 'message': preview,
                           'where': where.strip() or 'your chat',
                           'name': user.get_full_name() or user.username})
        except Exception:                                      # noqa: BLE001
            # Sending a message must never fail because a mail server did.
            pass


@login_required
@require_POST
def api_message_edit(request, pk):
    """Correct something you sent, within five minutes of sending it.

    Two refusals, said apart. "You cannot edit this" and "you can no longer edit
    this" are different situations for the person reading them: one is a rule
    they had not understood, the other is a deadline they have missed, and the
    second needs to say how long the window was or it reads as a fault.
    """
    msg = get_object_or_404(Message, pk=pk, conversation__participants=request.user)
    if not msg.can_edit(request.user):
        mine_to_edit = (msg.sender_id == request.user.pk and not msg.system)
        minutes = int(EDIT_WINDOW.total_seconds() // 60)
        return JsonResponse({'error': (
            f'That message is more than {minutes} minutes old, so it can no '
            'longer be edited.' if mine_to_edit
            else 'You can only edit your own messages.'),
            'expired': mine_to_edit}, status=403)
    text = (request.POST.get('text') or '').strip()
    if not text and not msg.attachment:
        return JsonResponse({'error': 'A message cannot be emptied.'}, status=400)
    msg.text = text
    msg.edited_at = timezone.now()
    msg.save(update_fields=['text', 'edited_at'])
    return JsonResponse({'message': _msg_json(msg, request.user)})


@login_required
@require_POST
def api_message_hide(request, pk):
    """Delete a message from my own view. Theirs is untouched."""
    msg = get_object_or_404(Message, pk=pk, conversation__participants=request.user)
    msg.hidden_for.add(request.user)
    return JsonResponse({'ok': True, 'id': msg.pk})


@login_required
@require_POST
def api_react(request, pk):
    """Toggle one emoji on one message."""
    msg = get_object_or_404(Message, pk=pk, conversation__participants=request.user)
    emoji = _clean_emoji(request.POST.get('emoji'))
    if not emoji:
        return JsonResponse({'error': 'That is not an emoji.'}, status=400)

    existing = Reaction.objects.filter(message=msg, user=request.user, emoji=emoji)
    if existing.exists():
        existing.delete()
    else:
        # One reaction per person per message, as WhatsApp does — picking a new
        # one replaces the old rather than stacking two from the same person.
        Reaction.objects.filter(message=msg, user=request.user).delete()
        Reaction.objects.create(message=msg, user=request.user, emoji=emoji)
    return JsonResponse({'id': msg.pk, 'reactions': msg.reaction_summary(request.user)})


# ── my view of a chat ────────────────────────────────────────────────────

@login_required
@require_POST
def api_pin(request, pk):
    conv = _mine(request, pk)
    state = conv.state_for(request.user)
    state.pinned = not state.pinned
    state.save(update_fields=['pinned'])
    return JsonResponse({'ok': True, 'pinned': state.pinned})


@login_required
@require_POST
def api_delete_chat(request, pk):
    """Delete for me. Their copy is untouched."""
    conv = _mine(request, pk)
    conv.state_for(request.user).clear()
    return JsonResponse({'ok': True, 'cleared': True})


@login_required
@require_POST
def api_mark_read(request, pk):
    conv = _mine(request, pk)
    conv.state_for(request.user).mark_read()
    return JsonResponse({'ok': True})


# ── groups ───────────────────────────────────────────────────────────────

@login_required
@require_POST
def api_group_create(request):
    name = (request.POST.get('name') or '').strip()
    if not name:
        return JsonResponse({'error': 'Give the group a name.'}, status=400)
    ids = [int(v) for v in request.POST.getlist('members') if str(v).isdigit()]
    people = list(User.objects.filter(pk__in=ids, is_active=True)
                  .exclude(pk=request.user.pk))
    if not people:
        return JsonResponse({'error': 'Add at least one other person.'}, status=400)

    # A second press, or a retried request, must not make a second group. One
    # careless double-click produced six identical groups; the button is guarded
    # in the browser now, but a network retry is not something the browser can
    # take back, so the same name from the same person within the minute is
    # treated as the request that already succeeded.
    cutoff = timezone.now() - timedelta(minutes=1)
    twin = (Conversation.objects
            .filter(kind=Conversation.Kind.GROUP, name=name[:120],
                    created_by=request.user, created_at__gte=cutoff)
            .order_by('-pk').first())
    if twin is not None:
        return JsonResponse({'ok': True, 'id': twin.pk, 'deduplicated': True,
                             'conversation': _conv_json(twin, request.user)})

    conv = Conversation.objects.create(kind=Conversation.Kind.GROUP,
                                       name=name[:120], created_by=request.user)
    conv.participants.add(request.user, *people)
    if request.FILES.get('photo'):
        conv.photo = request.FILES['photo']
        conv.save(update_fields=['photo'])
    who = request.user.get_full_name() or request.user.username
    _system(conv, request.user, f'{who} created “{conv.name}”')
    return JsonResponse({'ok': True, 'id': conv.pk,
                         'conversation': _conv_json(conv, request.user)})


@login_required
@require_POST
def api_group_update(request, pk):
    """Rename, change the picture, add or remove people, or leave."""
    conv = _mine(request, pk)
    if not conv.is_group:
        return JsonResponse({'error': 'That is not a group.'}, status=400)
    action = request.POST.get('action') or 'rename'
    me = request.user.get_full_name() or request.user.username

    if action == 'leave':
        # Anybody may leave, including the owner. A group whose owner has gone
        # is still readable; only an Intranet Admin can then change it.
        conv.participants.remove(request.user)
        _system(conv, request.user, f'{me} left the group')
        conv.state_for(request.user).clear()
        return JsonResponse({'ok': True, 'left': True})

    if not conv.is_admin(request.user):
        return JsonResponse({'error': 'Only the person who made the group can change it.'},
                            status=403)

    if action == 'rename':
        # One save for the three things that describe a group — its name, what it
        # is for, and its picture. They are edited together on one screen, and
        # three endpoints would mean three requests, three chances to half-fail
        # and three system notes for one press of Save.
        name = (request.POST.get('name') or '').strip()
        if not name:
            return JsonResponse({'error': 'Give the group a name.'}, status=400)
        if name[:120] != conv.name:
            old = conv.name
            conv.name = name[:120]
            conv.save(update_fields=['name'])
            _system(conv, request.user, f'{me} renamed “{old}” to “{conv.name}”')

        # Only when the field was actually sent. A caller that renames a group
        # without touching the description must not silently clear it.
        if 'description' in request.POST:
            about = (request.POST.get('description') or '').strip()[:280]
            if about != conv.description:
                conv.description = about
                conv.save(update_fields=['description'])
                # No system note. What a group is for is a caption on the group,
                # not an event in the conversation, and a line in everybody's chat
                # every time somebody fixes a typo in it is noise.

        if request.FILES.get('photo'):
            conv.photo = request.FILES['photo']
            conv.save(update_fields=['photo'])
            _system(conv, request.user, f'{me} changed the group picture')
        elif request.POST.get('photo_clear') == '1' and conv.photo:
            conv.photo.delete(save=False)
            conv.photo = None
            conv.save(update_fields=['photo'])
            _system(conv, request.user, f'{me} removed the group picture')
        return JsonResponse({'ok': True,
                             'conversation': _conv_json(conv, request.user)})

    if action == 'add':
        ids = [int(v) for v in request.POST.getlist('members') if str(v).isdigit()]
        already = set(conv.participants.values_list('pk', flat=True))
        people = [u for u in User.objects.filter(pk__in=ids, is_active=True)
                  if u.pk not in already]
        if not people:
            return JsonResponse({'error': 'Nobody new to add.'}, status=400)
        conv.participants.add(*people)
        names = ', '.join(u.get_full_name() or u.username for u in people)
        _system(conv, request.user, f'{me} added {names}')
        return JsonResponse({'ok': True, 'added': len(people),
                             'conversation': _conv_json(conv, request.user)})

    if action == 'remove':
        user = User.objects.filter(pk=request.POST.get('user')).first()
        if not user or user.pk == request.user.pk:
            return JsonResponse({'error': 'Use Leave to remove yourself.'}, status=400)
        conv.participants.remove(user)
        _system(conv, request.user,
                f'{me} removed {user.get_full_name() or user.username}')
        return JsonResponse({'ok': True, 'removed': user.pk,
                             'conversation': _conv_json(conv, request.user)})

    return JsonResponse({'error': 'Unknown action.'}, status=400)


# ── people ───────────────────────────────────────────────────────────────

@login_required
def api_people(request):
    """Colleagues to start a chat with, or add to a group.

    Each result carries whether that person is there right now. Searching for
    somebody in order to talk to them is exactly the moment it matters whether
    they are at their desk or in a call — and the picker was the one place on the
    Hub that showed a name with no sign of it either way.
    """
    exclude = [request.user]
    conv_id = request.GET.get('not_in')
    if conv_id and str(conv_id).isdigit():
        conv = Conversation.objects.filter(pk=conv_id,
                                           participants=request.user).first()
        if conv:
            exclude += list(conv.participants.all())
    people = search_people(request.GET.get('q', ''), exclude=exclude)
    # One extra query for the whole page of results, not one per row: see
    # presence_for. Added here rather than in core.people because presence is a
    # chat idea — it is calls that make somebody busy — and core does not know
    # about calls.
    standing = presence_payload([p['id'] for p in people])
    for p in people:
        at = standing.get(p['id']) or {}
        p['presence'] = at.get('presence', 'offline')
        p['presence_label'] = at.get('label', '')
    return JsonResponse({'users': people})


@login_required
def api_start_with(request, user_pk):
    other = get_object_or_404(User, pk=user_pk)
    conv = _direct_with(request.user, other)
    return JsonResponse({'conv_id': conv.pk, 'is_online': _online(other)})


# ── polling ──────────────────────────────────────────────────────────────

@login_required
def api_poll(request):
    """Everything that may have changed since the client last looked.

    One request rather than four: new messages anywhere, the badge count, and —
    for whichever conversation is open — which of my messages have now been
    seen and which bubbles have gained a reaction or an edit. Reactions and
    edits do not create new messages, so polling by "id greater than" would
    never notice them.
    """
    try:
        after_id = int(request.GET.get('after', 0))
    except (TypeError, ValueError):
        after_id = 0
    active = request.GET.get('conv')
    active = int(active) if str(active).isdigit() else None

    states = {s.conversation_id: s for s in ChatState.objects.filter(user=request.user)}
    conv_ids = list(request.user.conversations.values_list('pk', flat=True))

    # One grouped query rather than a COUNT per conversation every four seconds.
    total_unread = sum(_unread_map(request.user, conv_ids, states).values())

    incoming = (Message.objects
                .filter(conversation_id__in=conv_ids, pk__gt=after_id)
                .exclude(sender=request.user)
                .exclude(hidden_for=request.user)
                .select_related('sender', 'sender__employee', 'conversation',
                                'reply_to', 'reply_to__sender')
                .prefetch_related('reactions', 'files')
                .order_by('pk')[:50])

    new_msgs = []
    for m in incoming:
        state = states.get(m.conversation_id)
        if state and state.cleared_at and m.sent_at <= state.cleared_at:
            continue
        payload = _msg_json(m, request.user)
        payload['conv_id'] = m.conversation_id
        payload['conv_name'] = m.conversation.title_for(request.user)
        payload['is_group'] = m.conversation.is_group
        new_msgs.append(payload)

    out = {'total_unread': total_unread, 'new_messages': new_msgs}

    # The phone. Carried by this request rather than a timer of its own, so
    # hearing about a call costs one query on a poll that was happening anyway
    # rather than a second recurring request for every signed-in person. Imported
    # here because messaging.call_views reads this module's helpers, and at the
    # top the two would import each other.
    from .call_views import ring_for
    out['call'] = ring_for(request.user)

    # Who is there right now, for the rows the browser currently has on screen.
    # It rides on this request for the same reason the telephone does: presence
    # changes without a message to announce it, and a timer of its own would be a
    # second recurring request per signed-in person. The browser sends the ids it
    # is showing, so nothing is computed for a chat nobody is looking at.
    watching = _watched_ids(request.GET.get('watch'))
    if watching:
        out['presence'] = {str(uid): at
                           for uid, at in presence_payload(watching).items()}

    # Calls missed since the reader last opened the log. Only asked for when the
    # browser has a marker to compare against — see call_views.missed_since — and
    # answered from the calls newer than it, which is nothing almost every time.
    seen_call = request.GET.get('seen_call')
    if str(seen_call).isdigit():
        from .call_views import missed_since
        out['missed_calls'] = missed_since(request.user, int(seen_call))

    if active and active in conv_ids:
        conv = Conversation.objects.filter(pk=active).first()
        state = states.get(active)
        seen_before = conv.slowest_read_mark(request.user)
        visible = conv.visible_messages(request.user, state) if conv else Message.objects.none()
        out['seen_ids'] = list(
            visible.filter(sender=request.user,
                           sent_at__lte=seen_before).values_list('pk', flat=True)
        ) if seen_before else []
        # Only the bubbles that carry something a plain id check cannot see.
        # The window is the most recent messages, decided *before* filtering, and
        # its floor is reported: the client treats "not in touched" as "lost its
        # reactions", so it has to know how far back that conclusion is valid.
        # Filtering first and then taking 80 would have made a long conversation
        # silently drop the reactions off its older messages.
        window = list(visible.order_by('-pk').values_list('pk', flat=True)[:80])
        out['touched_from'] = min(window) if window else None
        touched = (Message.objects.filter(pk__in=window)
                   .filter(Q(edited_at__isnull=False) | Q(reactions__isnull=False))
                   .distinct().prefetch_related('reactions'))
        out['touched'] = [{
            'id': m.pk,
            'text': m.text,
            'edited_at': m.edited_at.isoformat() if m.edited_at else None,
            'reactions': m.reaction_summary(request.user),
        } for m in touched]

    return JsonResponse(out)
