"""The recognition feed, and what people do with a post in it.

A praise and a certification are the same social object — something good
happened to somebody — so they share a feed, a like button and a comment
thread. What differs is only how the card is drawn.
"""
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.comments import apply_delete, apply_edit
from core.mentions import notify_mentions
from core.models import NotificationEvent as Event
from core.models import user_is_hr
from core.notify import notify

from .models import Certification, Comment, Praise, Reaction, TaqdeerSettings


def _target(kind, pk, user):
    """The post being acted on, or 404. Hidden and unpublished ones are gone."""
    if kind == 'praise':
        p = get_object_or_404(Praise, pk=pk, hidden_at__isnull=True)
        if (TaqdeerSettings.get().praise_needs_approval
                and p.status != Praise.Status.APPROVED
                and not user_is_hr(user)):
            raise Http404
        return p, None
    if kind == 'certification':
        c = get_object_or_404(Certification, pk=pk)
        if not c.published and not user_is_hr(user):
            raise Http404
        return None, c
    raise Http404


def annotate(items, user):
    """Attach like and comment counts, and whether this person has liked.

    Done in two queries for the whole page rather than two per card: a feed of
    twenty posts was otherwise forty round trips.
    """
    praise_ids = [i.pk for i in items if isinstance(i, Praise)]
    cert_ids = [i.pk for i in items if isinstance(i, Certification)]

    likes = Reaction.objects.filter(praise_id__in=praise_ids)
    cert_likes = Reaction.objects.filter(certification_id__in=cert_ids)
    comments = Comment.objects.filter(praise_id__in=praise_ids,
                                      hidden_at__isnull=True)
    cert_comments = Comment.objects.filter(certification_id__in=cert_ids,
                                           hidden_at__isnull=True)

    def tally(rows, attr):
        out = {}
        mine = set()
        for r in rows:
            key = getattr(r, attr)
            out[key] = out.get(key, 0) + 1
            if getattr(r, 'user_id', None) == user.pk:
                mine.add(key)
        return out, mine

    def by_emoji(rows, attr):
        """{post: {emoji: count}} and {post: the emoji this person chose}."""
        counts, mine = {}, {}
        for r in rows:
            key = getattr(r, attr)
            counts.setdefault(key, {})
            counts[key][r.emoji] = counts[key].get(r.emoji, 0) + 1
            if r.user_id == user.pk:
                mine[key] = r.emoji
        return counts, mine

    ep, chose_p = by_emoji(likes, 'praise_id')
    ec, chose_c = by_emoji(cert_likes, 'certification_id')

    lp, mine_p = tally(likes, 'praise_id')
    lc, mine_c = tally(cert_likes, 'certification_id')
    cp, _ = tally(comments, 'praise_id')
    cc, _ = tally(cert_comments, 'certification_id')

    for item in items:
        if isinstance(item, Praise):
            item.kind = 'praise'
            item.n_likes = lp.get(item.pk, 0)
            item.n_comments = cp.get(item.pk, 0)
            item.liked = item.pk in mine_p
            item.emoji_counts = ep.get(item.pk, {})
            item.my_emoji = chose_p.get(item.pk)
        else:
            item.kind = 'certification'
            item.n_likes = lc.get(item.pk, 0)
            item.n_comments = cc.get(item.pk, 0)
            item.liked = item.pk in mine_c
            item.emoji_counts = ec.get(item.pk, {})
            item.my_emoji = chose_c.get(item.pk)
        # Ordered for the template: the four, with their counts.
        item.reactions = [(e, label, item.emoji_counts.get(e, 0), item.my_emoji == e)
                          for e, label in Reaction.CHOICES]
    return items


@login_required
@require_POST
def like(request, kind, pk):
    """Toggle a like. Answers JSON so the card updates without a page load."""
    praise, cert = _target(kind, pk, request.user)
    wanted = request.POST.get('emoji') or Reaction.HEART
    if wanted not in dict(Reaction.CHOICES):
        wanted = Reaction.HEART

    existing = Reaction.objects.filter(user=request.user, praise=praise,
                                       certification=cert).first()
    if existing and existing.emoji == wanted:
        # The same one again means take it back.
        existing.delete()
        liked = False
    elif existing:
        # A different one replaces it rather than adding a second, so the
        # counts always add up to the number of people who reacted.
        existing.emoji = wanted
        existing.save(update_fields=['emoji'])
        liked = True
    else:
        Reaction.objects.create(user=request.user, praise=praise,
                                certification=cert, emoji=wanted)
        liked = True
        # Tell the person it is about, once, and never about their own click.
        who = (praise.recipient if praise else cert.employee)
        subject = praise.subject if praise else cert.name
        actor_name = request.user.get_full_name() or request.user.username
        notify(who, Event.GENERAL,
               title=f'{actor_name} liked your recognition',
               body=subject, link='/taqdeer/', actor=request.user,
               case='recognition.liked',
               values={'actor': actor_name, 'subject_line': subject,
                       'name': who.get_full_name() or who.username})

    rows = Reaction.objects.filter(praise=praise, certification=cert)
    counts = {}
    for r in rows:
        counts[r.emoji] = counts.get(r.emoji, 0) + 1
    return JsonResponse({'liked': liked, 'emoji': wanted if liked else None,
                         'count': rows.count(), 'counts': counts})


@login_required
def post(request, kind, pk):
    """One post, its picture in full, and its comments.

    Fetched into a dialog rather than being its own page: the reader is in the
    middle of a feed and should come back to it, not navigate away and lose
    their place.
    """
    praise, cert = _target(kind, pk, request.user)
    item = praise or cert
    annotate([item], request.user)

    if request.method == 'POST':
        text = (request.POST.get('text') or '').strip()
        if text:
            comment = Comment.objects.create(author=request.user, praise=praise,
                                             certification=cert, text=text)
            name = request.user.get_full_name() or request.user.username

            # Anyone named is pulled in. Shared with news comments and Exchange
            # replies rather than reimplemented, so @ means the same thing and
            # respects the same per-person channels everywhere in the Hub.
            named = notify_mentions(
                text, request.user, title=f'{name} mentioned you in a comment',
                link='/taqdeer/', body=text,
                case='recognition.mentioned',
                values={'actor': name, 'comment': text})
            if named:
                comment.mentions.set(named)

            # The person it is about hears once: never about their own comment,
            # and not a second time when the comment already named them.
            who = (praise.recipient if praise else cert.employee)
            if who != request.user and who not in named:
                notify(who, Event.GENERAL,
                       title=f'{name} commented on your recognition',
                       body=text[:180], link='/taqdeer/', actor=request.user,
                       case='recognition.commented',
                       values={'actor': name, 'comment': text,
                               'name': who.get_full_name() or who.username})
            annotate([item], request.user)

    comments = (Comment.objects
                .filter(praise=praise, certification=cert, hidden_at__isnull=True)
                .select_related('author', 'author__employee'))

    # Who may do what to this one, worked out here rather than asked twice in
    # the template. Editing is the giver's; withdrawing is theirs or HR's.
    mine = bool(praise and praise.giver_id == request.user.pk)
    return render(request, 'taqdeer/_post.html', {
        'item': item, 'kind': kind, 'praise': praise, 'cert': cert,
        'comments': comments, 'is_hr': user_is_hr(request.user),
        'can_edit_praise': mine,
        'can_withdraw_praise': bool(praise) and (mine or user_is_hr(request.user)),
    })


@login_required
@require_POST
def comment_edit(request, pk):
    """Edit a comment in the recognition thread.

    Answers with the refreshed dialog rather than a redirect, because the reader
    is inside a dialog over the feed and a redirect would throw them back to the
    top of the page they were already on.
    """
    comment = get_object_or_404(Comment, pk=pk, hidden_at__isnull=True)
    if apply_edit(request, comment, 'text'):
        name = request.user.get_full_name() or request.user.username
        comment.mentions.set(notify_mentions(
            comment.text, request.user,
            title=f'{name} mentioned you in a comment',
            link='/taqdeer/', body=comment.text,
            case='recognition.mentioned',
            values={'actor': name, 'comment': comment.text}))
    kind = 'praise' if comment.praise_id else 'certification'
    return post(request, kind, comment.praise_id or comment.certification_id)


@login_required
@require_POST
def praise_edit(request, pk):
    """The giver rewrites their own Kudos.

    The author alone — not HR. HR can take one down, which is moderation; there
    is no version of moderation that involves putting different words in
    somebody else's mouth and leaving their name on it.

    Answers with the refreshed dialog, like comment_edit above, because the
    reader is inside a dialog over the feed.
    """
    p = get_object_or_404(Praise, pk=pk, hidden_at__isnull=True)
    if p.giver_id != request.user.pk:
        raise PermissionDenied

    subject = (request.POST.get('subject') or '').strip()[:140]
    message = (request.POST.get('message') or '').strip()
    # An empty box is a mistake, not an instruction to erase the Kudos —
    # withdrawing is its own button. Left unchanged rather than blanked.
    if subject and message:
        changed = (subject != p.subject or message != p.message
                   or (request.POST.get('category') or '').strip() != p.category
                   or (request.POST.get('project') or '').strip() != p.project)
        if changed:
            p.subject = subject
            p.message = message
            p.category = (request.POST.get('category') or '').strip()[:60]
            p.project = (request.POST.get('project') or '').strip()[:160]
            p.edited_at = timezone.now()
            p.save(update_fields=['subject', 'message', 'category', 'project',
                                  'edited_at'])
    return post(request, 'praise', p.pk)


@login_required
@require_POST
def comment_delete(request, pk):
    comment = get_object_or_404(Comment, pk=pk)
    kind = 'praise' if comment.praise_id else 'certification'
    target = comment.praise_id or comment.certification_id
    apply_delete(request, comment)
    return post(request, kind, target)
