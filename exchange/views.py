# exchange/views.py
"""AJi Exchange.

The shape of the permission rules matters more than any single view here, so
they are stated once and reused:

* **read** — a public community is readable by anyone signed in; a private one
  by its members and by admins. A private community is still *listed*, so the
  detail page has a locked branch rather than a 404: telling someone a space
  exists and they may ask to join is more useful than pretending it does not.
* **post** — members only, in both kinds.
* **admin** — the creator and the Intranet Admins. Renaming, membership,
  starring and deletion.
* **share to chat** — you may send a thread to anyone you could send a chat
  message to, *provided you can read it yourself*. For a private community that
  means the recipient must be a member too, or sharing would be a way to leak
  the thing membership was protecting.

Every write is POST-only and every one of them re-derives the permission from
the object, never from a hidden field.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.comments import apply_delete, apply_edit
from core.mentions import notify_mentions
from core.models import Notification, is_intranet_admin
from .models import (MAX_ATTACHMENT_BYTES, Attachment, Community, JoinRequest,
                     Thread, ThreadReply)


# ── helpers ──────────────────────────────────────────────────────────────

def _community(slug, include_deleted=False):
    manager = Community.all_objects if include_deleted else Community.objects
    return get_object_or_404(manager, slug=slug)


def _wants_json(request):
    return (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or 'application/json' in (request.headers.get('Accept') or ''))


def _flash(request, level, text):
    """A flash message, but only when the reader is about to see a page.

    A script calling one of these endpoints gets JSON back and never renders
    the queue, so the message sits there and pops up on whatever page they open
    next — "Starred." arriving twenty minutes later on the cafeteria menu.
    """
    if not _wants_json(request):
        getattr(messages, level)(request, text)


def _reply(request, ok_payload, redirect_to, error=None, status=400):
    """One answer for both a fetch() and a plain form post.

    Everything here has a working no-JavaScript path, so a view cannot simply
    return JSON — that is how the branding screen ended up showing a reader a
    page of {"ok": true}.
    """
    if error:
        if _wants_json(request):
            return JsonResponse({'error': error}, status=status)
        messages.error(request, error)
        return redirect(redirect_to)
    if _wants_json(request):
        return JsonResponse(dict(ok=True, **ok_payload))
    return redirect(redirect_to)


def _notify(recipient, actor, title, link, body=''):
    """One in-app notification. No email anywhere in the Hub."""
    if not recipient or recipient == actor:
        return
    Notification.objects.create(
        recipient=recipient, type=Notification.Type.INFO,
        title=title[:200], body=body[:500], link=link)


def _members_from_post(request, field='members'):
    """The users named by a member picker, as a list, ignoring unknown ids."""
    raw = request.POST.getlist(field)
    ids = []
    for value in raw:
        for part in str(value).split(','):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
    if not ids:
        return []
    return list(User.objects.filter(pk__in=ids, is_active=True))


def _starred(community):
    """What the "Starred Threads / Documents" panel shows, in one place.

    Both lists are capped: the panel is a third of the page, and a community
    that stars forty things has turned the panel into a second thread list.
    """
    return {
        'starred_threads': list(community.threads.filter(pinned=True)
                                .select_related('author')[:12]),
        'starred_docs': list(community.attachments.filter(pinned=True)
                             .select_related('uploaded_by')[:12]),
    }


def _recent_threads(community, exclude_pk=None, limit=8):
    """What has been posted here lately, newest first.

    Explicitly by date: Thread.Meta orders pinned-first, which is what a thread
    list wants and the opposite of what "recently" means.
    """
    qs = community.threads.select_related('author')
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return list(qs.annotate(n_replies=Count('replies'))
                .order_by('-created_at')[:limit])


def _attach_file(community, thread, upload, user, description=''):
    """Store one uploaded file as an Attachment. Returns (attachment, error)."""
    if upload.size > MAX_ATTACHMENT_BYTES:
        mb = upload.size / 1048576
        return None, (f'“{upload.name}” is {mb:.1f} MB. The limit is 35 MB — '
                      f'link to it on SharePoint instead, or compress it.')
    return Attachment.objects.create(
        community=community, thread=thread, name=upload.name[:300],
        description=description[:2000], file=upload,
        attach_type=Attachment.kind_for(upload.name),
        uploaded_by=user), None


# ── the index ────────────────────────────────────────────────────────────

@login_required
def exchange_home(request):
    """Rendered by the AJi Exchange feature block on the builder page.

    Both lists are annotated rather than counted per card: this used to walk
    `c.members.all()` once per community to decide which list it belonged in,
    and then again in the template for the count.
    """
    mine_ids = set(request.user.communities.values_list('pk', flat=True))
    everything = (Community.objects
                  .annotate(n_threads=Count('threads', distinct=True),
                            n_members=Count('members', distinct=True))
                  .order_by('name'))

    my_communities, disc_communities = [], []
    for c in everything:
        c.joined = c.pk in mine_ids
        # A private community you are not in: listed, so you know it exists and
        # can ask, but the card does not link through to anything readable.
        c.locked = c.is_private and not c.joined and not c.is_admin(request.user)
        (my_communities if c.joined else disc_communities).append(c)

    asked = set(request.user.community_join_requests
                .filter(status=JoinRequest.Status.PENDING)
                .values_list('community_id', flat=True))
    for c in disc_communities:
        c.requested = c.pk in asked

    return render(request, 'exchange/exchange_home.html', {
        'my_communities': my_communities,
        'disc_communities': disc_communities,
        'can_restore': is_intranet_admin(request.user),
        'deleted_count': Community.all_objects.filter(
            deleted_at__isnull=False).count() if is_intranet_admin(request.user) else 0,
    })


# ── one community ────────────────────────────────────────────────────────

@login_required
def community_detail(request, slug):
    community = _community(slug)
    is_member = community.is_member(request.user)
    is_admin = community.is_admin(request.user)
    can_read = community.can_read(request.user)

    if request.method == 'POST' and request.POST.get('action') == 'join':
        return _join_or_leave(request, community, is_member)

    ctx = {
        'community': community,
        'is_member': is_member,
        'is_admin': is_admin,
        'can_read': can_read,
        'tab': request.GET.get('tab', 'threads'),
    }

    if not can_read:
        # Listed but locked. Everything the card already showed, plus the ask.
        ctx.update({
            'member_count': community.member_count(),
            'thread_count': community.threads.count(),
            'my_request': community.join_requests.filter(user=request.user).first(),
        })
        return render(request, 'exchange/community_locked.html', ctx)

    ctx.update(_starred(community))
    ctx.update({
        'threads': (community.threads.select_related('author')
                    .annotate(n_replies=Count('replies', distinct=True),
                              n_files=Count('attachments', distinct=True))),
        'attachments': community.attachments.select_related('uploaded_by', 'thread'),
        'community_members': community.members.select_related('employee')
                                      .order_by('first_name', 'last_name'),
        'pending_requests': (community.join_requests
                             .filter(status=JoinRequest.Status.PENDING)
                             .select_related('user') if is_admin else []),
    })
    return render(request, 'exchange/community_detail.html', ctx)


def _join_or_leave(request, community, is_member):
    """The Join / Leave button. A private community cannot be self-joined."""
    if is_member:
        community.members.remove(request.user)
        # Leaving the community you created would leave it adminless in
        # practice, so say what happened rather than silently keeping the seat.
        _flash(request, 'success', f'You left {community.name}.')
    elif community.is_private and not community.is_admin(request.user):
        return _request_join(request, community)
    else:
        community.members.add(request.user)
        _flash(request, 'success', f'You joined {community.name}.')
    return redirect('community_detail', slug=community.slug)


@login_required
@require_POST
def community_join_request(request, slug):
    return _request_join(request, _community(slug))


def _request_join(request, community):
    if community.is_member(request.user):
        return redirect('community_detail', slug=community.slug)
    note = (request.POST.get('message') or '').strip()[:300]
    req, created = JoinRequest.objects.get_or_create(
        community=community, user=request.user,
        defaults={'message': note})
    if not created:
        # Asking again after a decline should reopen it, not sit declined
        # forever with no way back.
        req.status = JoinRequest.Status.PENDING
        req.message = note or req.message
        req.resolved_at = None
        req.resolved_by = None
        req.save(update_fields=['status', 'message', 'resolved_at', 'resolved_by'])

    who = request.user.get_full_name() or request.user.username
    link = reverse('community_detail', args=[community.slug]) + '?tab=members'
    for admin in _admins_of(community):
        _notify(admin, request.user, f'{who} asked to join {community.name}',
                link, note)
    _flash(request, 'success', 'Your request has been sent to the community owner.')
    return redirect('community_detail', slug=community.slug)


def _admins_of(community):
    """Who to notify about this community. The creator, or the Intranet Admins
    when there is no creator on the record."""
    if community.created_by_id:
        return [community.created_by]
    from django.contrib.auth.models import Group
    return list(User.objects.filter(
        Q(is_superuser=True) | Q(groups__name='Intranet Admin'),
        is_active=True).distinct()[:10])


@login_required
@require_POST
def join_request_action(request, slug, pk, action):
    """A community admin approving or declining a request."""
    community = _community(slug)
    if not community.is_admin(request.user):
        return _reply(request, {}, reverse('community_detail', args=[slug]),
                      error='Only the community owner can answer requests.',
                      status=403)
    req = get_object_or_404(JoinRequest, pk=pk, community=community)
    approved = action == 'approve'
    req.status = (JoinRequest.Status.APPROVED if approved
                  else JoinRequest.Status.DECLINED)
    req.resolved_at = timezone.now()
    req.resolved_by = request.user
    req.save(update_fields=['status', 'resolved_at', 'resolved_by'])
    if approved:
        community.members.add(req.user)
    _notify(req.user, request.user,
            f'{community.name}: request {"approved" if approved else "declined"}',
            reverse('community_detail', args=[slug]))
    _flash(request, 'success', f'Request {"approved" if approved else "declined"}.')
    return _reply(request, {'status': req.status},
                  reverse('community_detail', args=[slug]) + '?tab=members')


# ── membership ───────────────────────────────────────────────────────────

@login_required
@require_POST
def community_members_edit(request, slug):
    """Add or remove members. Admins only."""
    community = _community(slug)
    back = reverse('community_detail', args=[slug]) + '?tab=members'
    if not community.is_admin(request.user):
        return _reply(request, {}, back,
                      error='Only the community owner can change the members.',
                      status=403)

    action = request.POST.get('action')
    if action == 'remove':
        user = User.objects.filter(pk=request.POST.get('user')).first()
        if user:
            community.members.remove(user)
            messages.success(
                request,
                f'{user.get_full_name() or user.username} removed from the community.')
        return _reply(request, {'removed': bool(user)}, back)

    added = _members_from_post(request)
    new = [u for u in added if not community.members.filter(pk=u.pk).exists()]
    if new:
        community.members.add(*new)
        link = reverse('community_detail', args=[slug])
        for user in new:
            _notify(user, request.user,
                    f'You were added to {community.name}', link,
                    community.description[:300])
        _flash(request, 'success', f'{len(new)} member(s) added.')
    else:
        _flash(request, 'info', 'Nobody new to add.')
    return _reply(request, {'added': len(new)}, back)


@login_required
def community_member_search(request, slug):
    """People not already in this community, for the add-members picker."""
    community = _community(slug)
    if not community.can_read(request.user):
        return JsonResponse({'users': []}, status=403)
    return JsonResponse({'users': _people(request.GET.get('q', ''),
                                          exclude=community.members.all())})


@login_required
def people_search(request):
    """The same picker, on the create-community screen where there is no
    community to exclude against yet."""
    return JsonResponse({'users': _people(request.GET.get('q', ''),
                                          exclude=[request.user])})


def _people(q, exclude=(), restrict_to=None):
    """Delegates to core.people, which every picker on the Hub now shares.

    This used to be its own implementation and matched slightly different
    fields from the @mention autocomplete, so the same query found different
    people depending on which box you typed it into.
    """
    from core.people import search_people
    return search_people(q, exclude=exclude, restrict_to=restrict_to)


# ── creating, editing and removing a community ───────────────────────────

@login_required
def create_community(request):
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        description = (request.POST.get('description') or '').strip()
        icon = (request.POST.get('icon') or '').strip()[:50]
        visibility = request.POST.get('visibility')
        if visibility not in dict(Community.Visibility.choices):
            visibility = Community.Visibility.PUBLIC
        chosen = _members_from_post(request)

        error = None
        if not name:
            error = 'Give the community a name.'
        elif visibility == Community.Visibility.PRIVATE and not chosen:
            # A private community with only its creator in it is a private note.
            # Asking for at least one other person up front is the difference
            # between "private" and "empty".
            error = ('A private community needs at least one other member. '
                     'Search for a colleague above, or make it public.')

        if error:
            messages.error(request, error)
            return render(request, 'exchange/create_community.html', {
                'form_name': name, 'form_description': description,
                'form_icon': icon, 'form_visibility': visibility,
                'form_members': chosen,
                'visibilities': Community.Visibility.choices,
            })

        community = Community.objects.create(
            name=name[:200], slug=Community.unique_slug(name),
            description=description, icon=icon, visibility=visibility,
            created_by=request.user,
        )
        community.members.add(request.user, *chosen)
        link = reverse('community_detail', args=[community.slug])
        for user in chosen:
            _notify(user, request.user,
                    f'You were added to {community.name}', link, description[:300])
        _flash(request, 'success', f'“{community.name}” created.')
        return redirect('community_detail', slug=community.slug)

    return render(request, 'exchange/create_community.html', {
        'form_visibility': Community.Visibility.PUBLIC,
        'form_members': [],
        'visibilities': Community.Visibility.choices,
    })


@login_required
@require_POST
def community_update(request, slug):
    """Rename, re-describe, change visibility or change the icon. Admins only."""
    community = _community(slug)
    back = reverse('community_detail', args=[slug]) + '?tab=about'
    if not community.is_admin(request.user):
        return _reply(request, {}, back,
                      error='Only the community owner can change this.', status=403)

    name = (request.POST.get('name') or '').strip()
    if not name:
        return _reply(request, {}, back, error='Give the community a name.')
    community.name = name[:200]
    community.description = (request.POST.get('description') or '').strip()
    if 'icon' in request.POST:
        community.icon = (request.POST.get('icon') or '').strip()[:50]
    visibility = request.POST.get('visibility')
    if visibility in dict(Community.Visibility.choices):
        community.visibility = visibility
    community.save(update_fields=['name', 'description', 'icon', 'visibility'])
    _flash(request, 'success', 'Community updated.')
    return _reply(request, {'name': community.name}, back)


@login_required
@require_POST
def community_delete(request, slug):
    """Recoverable removal, by the creator or an Intranet Admin."""
    community = _community(slug)
    if not community.is_admin(request.user):
        return _reply(request, {}, reverse('community_detail', args=[slug]),
                      error='Only the community owner can delete it.', status=403)
    community.soft_delete(by=request.user)
    messages.success(
        request,
        f'“{community.name}” was removed. Its threads are kept — an Intranet '
        f'Admin can restore it from the removed list.')
    return _reply(request, {'deleted': True}, reverse('exchange_home'))


@login_required
def community_bin(request):
    """Removed communities, and the way back. Intranet Admins only."""
    if not is_intranet_admin(request.user):
        messages.error(request, 'Only an Intranet Admin can see removed communities.')
        return redirect('exchange_home')
    removed = (Community.all_objects.filter(deleted_at__isnull=False)
               .select_related('deleted_by', 'created_by')
               .annotate(n_threads=Count('threads', distinct=True),
                         n_members=Count('members', distinct=True))
               .order_by('deleted_at'))
    return render(request, 'exchange/community_bin.html', {
        'removed': removed,
        'purge_after_days': Community.PURGE_AFTER_DAYS,
    })


@login_required
@require_POST
def community_restore(request, slug):
    if not is_intranet_admin(request.user):
        return _reply(request, {}, reverse('exchange_home'),
                      error='Only an Intranet Admin can restore a community.',
                      status=403)
    community = _community(slug, include_deleted=True)
    community.restore()
    _flash(request, 'success', f'“{community.name}” restored.')
    return _reply(request, {'restored': True},
                  reverse('community_detail', args=[community.slug]))


@login_required
@require_POST
def community_purge(request, slug):
    """Destroy a removed community for good. Intranet Admins only.

    Reachable only for something already in the removed list. Permanent deletion
    is deliberately not one click from a live community — it is one click from
    something an admin has already chosen to remove, and has then looked at a
    second time on a page whose whole subject is removal.
    """
    if not is_intranet_admin(request.user):
        return _reply(request, {}, reverse('exchange_home'),
                      error='Only an Intranet Admin can delete a community for good.',
                      status=403)
    community = _community(slug, include_deleted=True)
    if community.deleted_at is None:
        return _reply(request, {}, reverse('community_detail', args=[community.slug]),
                      error='That community has not been removed, so there is '
                            'nothing to clear. Remove it first.',
                      status=400)
    name = community.name
    community.hard_delete()
    _flash(request, 'success',
           f'“{name}” and everything in it were deleted permanently.')
    return _reply(request, {'purged': True}, reverse('community_bin'))


# ── threads ──────────────────────────────────────────────────────────────

@login_required
def thread_detail(request, slug, pk):
    community = _community(slug)
    if not community.can_read(request.user):
        messages.error(request, 'That community is private.')
        return redirect('community_detail', slug=slug)

    thread = get_object_or_404(Thread, pk=pk, community=community)
    replies = thread.replies.select_related('author').prefetch_related('mentions')

    if request.method == 'POST':
        if not community.can_post(request.user):
            messages.error(request, 'Join the community to reply.')
            return redirect('thread_detail', slug=slug, pk=pk)
        body = (request.POST.get('body') or '').strip()
        if body:
            reply = ThreadReply.objects.create(
                thread=thread, author=request.user, body=body)
            who = request.user.get_full_name() or request.user.username
            notified = notify_mentions(
                body, request.user,
                title=f"{who} mentioned you in {thread.title}",
                link=reverse('thread_detail', args=[slug, pk]),
                body=body,
                case='exchange.mentioned',
                values={'actor': who, 'thread': thread.title, 'comment': body},
            )
            if notified:
                reply.mentions.set(notified)
        return redirect('thread_detail', slug=slug, pk=pk)

    attachments = list(thread.attachments.select_related('uploaded_by'))

    ctx = {
        'community': community,
        'thread': thread,
        'replies': replies,
        'attachments': attachments,
        # Images get a thumbnail grid and files get a list; splitting them here
        # keeps the template from asking the same question twice.
        'images': [a for a in attachments if a.is_image and a.file],
        'files': [a for a in attachments if not (a.is_image and a.file)],
        # What else is in here, newest first. The right-hand column used to hold
        # only the starred panel, so on a community that has starred nothing it
        # was a thousand pixels of blank paper beside the conversation.
        'more_threads': _recent_threads(community, exclude_pk=thread.pk),
        'is_member': community.is_member(request.user),
        'is_admin': community.is_admin(request.user),
        'can_edit': thread.can_edit(request.user),
        'can_delete': thread.can_delete(request.user),
        'max_mb': MAX_ATTACHMENT_BYTES // 1048576,
    }
    ctx.update(_starred(community))
    # A link to the page you are already on is not a suggestion. If it was the
    # only starred thing, the guard in the template drops the card entirely.
    ctx['starred_threads'] = [t for t in ctx['starred_threads'] if t.pk != thread.pk]
    return render(request, 'exchange/thread_detail.html', ctx)


@login_required
def create_thread(request, slug):
    community = _community(slug)
    if not community.can_post(request.user):
        messages.error(request, 'Join the community before posting.')
        return redirect('community_detail', slug=slug)

    if request.method == 'POST':
        title = (request.POST.get('title') or '').strip()
        body = (request.POST.get('body') or '').strip()
        thread_type = request.POST.get('thread_type', 'discussion')
        if thread_type not in dict(Thread.ThreadType.choices):
            thread_type = Thread.ThreadType.DISCUSSION
        uploads = request.FILES.getlist('attachments')

        # Reject an oversized file before writing the thread, so a rejected
        # upload does not leave a half-posted thread behind.
        too_big = [f for f in uploads if f.size > MAX_ATTACHMENT_BYTES]
        error = None
        if not title or not body:
            error = 'A thread needs a title and something in the body.'
        elif too_big:
            names = ', '.join(f'“{f.name}” ({f.size / 1048576:.1f} MB)' for f in too_big)
            error = f'Too large: {names}. The limit is 35 MB per file.'

        if error:
            messages.error(request, error)
            return render(request, 'exchange/create_thread.html', {
                'community': community,
                'thread_types': Thread.ThreadType.choices,
                'form_title': title, 'form_body': body,
                'form_type': thread_type,
                'recent_threads': _recent_threads(community),
                'max_mb': MAX_ATTACHMENT_BYTES // 1048576,
            })

        thread = Thread.objects.create(
            community=community, title=title[:300], body=body,
            author=request.user, thread_type=thread_type)
        for upload in uploads:
            _attach_file(community, thread, upload, request.user)
        return redirect('thread_detail', slug=slug, pk=thread.pk)

    return render(request, 'exchange/create_thread.html', {
        'community': community,
        'thread_types': Thread.ThreadType.choices,
        'form_type': Thread.ThreadType.DISCUSSION,
        'recent_threads': _recent_threads(community),
        'max_mb': MAX_ATTACHMENT_BYTES // 1048576,
    })


@login_required
def thread_edit(request, slug, pk):
    """The author rewriting their own thread, and managing its files."""
    community = _community(slug)
    thread = get_object_or_404(Thread, pk=pk, community=community)
    if not thread.can_edit(request.user):
        messages.error(request, 'Only the person who posted a thread can edit it.')
        return redirect('thread_detail', slug=slug, pk=pk)

    if request.method == 'POST':
        title = (request.POST.get('title') or '').strip()
        body = (request.POST.get('body') or '').strip()
        thread_type = request.POST.get('thread_type', thread.thread_type)
        uploads = request.FILES.getlist('attachments')
        drop = [int(v) for v in request.POST.getlist('remove_attachment') if str(v).isdigit()]

        too_big = [f for f in uploads if f.size > MAX_ATTACHMENT_BYTES]
        error = None
        if not title or not body:
            error = 'A thread needs a title and something in the body.'
        elif too_big:
            names = ', '.join(f'“{f.name}”' for f in too_big)
            error = f'Too large: {names}. The limit is 35 MB per file.'

        if not error:
            thread.title = title[:300]
            thread.body = body
            if thread_type in dict(Thread.ThreadType.choices):
                thread.thread_type = thread_type
            thread.edited_at = timezone.now()
            thread.save(update_fields=['title', 'body', 'thread_type',
                                       'edited_at', 'updated_at'])
            if drop:
                thread.attachments.filter(pk__in=drop).delete()
            for upload in uploads:
                _attach_file(community, thread, upload, request.user)
            _flash(request, 'success', 'Thread updated.')
            return redirect('thread_detail', slug=slug, pk=pk)
        messages.error(request, error)

    return render(request, 'exchange/thread_edit.html', {
        'community': community,
        'thread': thread,
        'attachments': thread.attachments.all(),
        'thread_types': Thread.ThreadType.choices,
        'recent_threads': _recent_threads(community, exclude_pk=thread.pk, limit=6),
        'max_mb': MAX_ATTACHMENT_BYTES // 1048576,
    })


@login_required
@require_POST
def thread_delete(request, slug, pk):
    community = _community(slug)
    thread = get_object_or_404(Thread, pk=pk, community=community)
    if not thread.can_delete(request.user):
        return _reply(request, {}, reverse('thread_detail', args=[slug, pk]),
                      error='You cannot delete this thread.', status=403)
    title = thread.title
    thread.delete()
    _flash(request, 'success', f'“{title}” was deleted.')
    return _reply(request, {'deleted': True},
                  reverse('community_detail', args=[slug]))


@login_required
@require_POST
def thread_pin(request, slug, pk):
    """Star a thread into the side panel. Community admins only."""
    community = _community(slug)
    if not community.is_admin(request.user):
        return _reply(request, {}, reverse('thread_detail', args=[slug, pk]),
                      error='Only the community owner can star threads.', status=403)
    thread = get_object_or_404(Thread, pk=pk, community=community)
    thread.pinned = not thread.pinned
    thread.save(update_fields=['pinned'])
    _flash(request, 'success', 'Starred.' if thread.pinned else 'Unstarred.')
    return _reply(request, {'pinned': thread.pinned},
                  reverse('thread_detail', args=[slug, pk]))


# ── documents ────────────────────────────────────────────────────────────

@login_required
@require_POST
def attachment_add(request, slug):
    """A file or a link added straight to the Documents tab."""
    community = _community(slug)
    back = reverse('community_detail', args=[slug]) + '?tab=docs'
    if not community.can_post(request.user):
        return _reply(request, {}, back, error='Join the community first.', status=403)

    url = (request.POST.get('url') or '').strip()
    description = (request.POST.get('description') or '').strip()
    uploads = request.FILES.getlist('file')

    if url and not uploads:
        if not url.startswith(('http://', 'https://', '/')):
            return _reply(request, {}, back,
                          error='A link must start with https:// or /.')
        Attachment.objects.create(
            community=community, name=(request.POST.get('name') or url)[:300],
            description=description, url=url,
            attach_type=Attachment.AttachType.LINK, uploaded_by=request.user)
        _flash(request, 'success', 'Link added.')
        return _reply(request, {'added': 1}, back)

    if not uploads:
        return _reply(request, {}, back, error='Choose a file, or paste a link.')

    added, problems = 0, []
    for upload in uploads:
        obj, err = _attach_file(community, None, upload, request.user, description)
        if err:
            problems.append(err)
        else:
            added += 1
    if problems:
        for p in problems:
            messages.error(request, p)
    if added:
        _flash(request, 'success', f'{added} file(s) added.')
    return _reply(request, {'added': added}, back)


@login_required
@require_POST
def attachment_pin(request, slug, pk):
    community = _community(slug)
    back = reverse('community_detail', args=[slug]) + '?tab=docs'
    if not community.is_admin(request.user):
        return _reply(request, {}, back,
                      error='Only the community owner can star documents.', status=403)
    att = get_object_or_404(Attachment, pk=pk, community=community)
    att.pinned = not att.pinned
    att.save(update_fields=['pinned'])
    return _reply(request, {'pinned': att.pinned}, back)


@login_required
@require_POST
def attachment_delete(request, slug, pk):
    community = _community(slug)
    back = reverse('community_detail', args=[slug]) + '?tab=docs'
    att = get_object_or_404(Attachment, pk=pk, community=community)
    if not (community.is_admin(request.user) or att.uploaded_by_id == request.user.pk):
        return _reply(request, {}, back,
                      error='You cannot remove this document.', status=403)
    att.delete()
    _flash(request, 'success', 'Document removed.')
    return _reply(request, {'deleted': True}, back)


# ── sharing a thread into chat ───────────────────────────────────────────

@login_required
def thread_share_targets(request, slug, pk):
    """Who this thread may be sent to.

    For a public community, anybody. For a private one, only fellow members —
    sending a private thread to an outsider would hand them a link that either
    leaks the content or 403s, and neither is a good answer.
    """
    community = _community(slug)
    if not community.can_read(request.user):
        return JsonResponse({'users': [], 'error': 'Not allowed.'}, status=403)
    get_object_or_404(Thread, pk=pk, community=community)

    q = request.GET.get('q', '')
    if community.is_private:
        # The pool itself is the gate: for a private community the picker can
        # only ever offer members, so a mistake here cannot become a leak.
        return JsonResponse({'users': _people(q, exclude=[request.user],
                                              restrict_to=community.members.all()),
                             'scope': 'members'})

    return JsonResponse({'users': _people(q, exclude=[request.user]),
                         'scope': 'everyone'})


@login_required
@require_POST
def thread_share(request, slug, pk):
    """Send this thread to colleagues as a chat message with a link."""
    from messaging.models import Conversation, Message

    community = _community(slug)
    back = reverse('thread_detail', args=[slug, pk])
    if not community.can_read(request.user):
        return _reply(request, {}, reverse('community_detail', args=[slug]),
                      error='That community is private.', status=403)
    thread = get_object_or_404(Thread, pk=pk, community=community)

    recipients = _members_from_post(request, 'to')
    if not recipients:
        return _reply(request, {}, back, error='Choose at least one person.')

    if community.is_private:
        allowed_ids = set(community.members.values_list('pk', flat=True))
        blocked = [u for u in recipients if u.pk not in allowed_ids]
        if blocked:
            names = ', '.join(u.get_full_name() or u.username for u in blocked)
            return _reply(
                request, {}, back, status=403,
                error=(f'{community.name} is private, so this thread can only be '
                       f'sent to its members. Not a member: {names}.'))

    link = request.build_absolute_uri(back)
    note = (request.POST.get('note') or '').strip()[:500]
    who = request.user.get_full_name() or request.user.username
    text = (f'{note}\n\n' if note else '') + \
           f'📌 {thread.title}\n{community.name} · AJi Exchange\n{link}'

    sent = 0
    for user in recipients:
        if user == request.user:
            continue
        conv = (Conversation.objects.filter(participants=request.user)
                .filter(participants=user).first())
        if not conv:
            conv = Conversation.objects.create()
            conv.participants.add(request.user, user)
        Message.objects.create(conversation=conv, sender=request.user, text=text)
        conv.save()  # move it to the top of their list
        _notify(user, request.user, f'{who} shared a thread with you',
                reverse('conversation_detail', args=[conv.pk]), thread.title)
        sent += 1

    _flash(request, 'success', f'Shared with {sent} colleague(s).')
    return _reply(request, {'sent': sent}, back)


@login_required
@require_POST
def reply_edit(request, pk):
    """The author fixes their own reply."""
    reply = get_object_or_404(
        ThreadReply.objects.select_related('thread', 'thread__community'), pk=pk)
    if apply_edit(request, reply, 'body'):
        who = request.user.get_full_name() or request.user.username
        notified = notify_mentions(
            reply.body, request.user,
            title=f'{who} mentioned you in a reply',
            link=reverse('thread_detail', args=[reply.thread.community.slug,
                                                reply.thread.pk]),
            body=reply.body,
            case='exchange.mentioned',
            values={'actor': who, 'thread': reply.thread.title,
                    'comment': reply.body})
        reply.mentions.set(notified)
        messages.success(request, 'Reply updated.')
    return redirect('thread_detail', slug=reply.thread.community.slug,
                    pk=reply.thread.pk)


@login_required
@require_POST
def reply_delete(request, pk):
    reply = get_object_or_404(
        ThreadReply.objects.select_related('thread', 'thread__community'), pk=pk)
    slug, thread_pk = reply.thread.community.slug, reply.thread.pk
    apply_delete(request, reply)
    messages.success(request, 'Reply deleted.')
    return redirect('thread_detail', slug=slug, pk=thread_pk)
