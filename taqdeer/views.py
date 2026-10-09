"""AJi Taqdeer — the wall, the Kudos composer, and the Athar Award workflow."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.core.files.base import ContentFile
from django.db.models import Count, Q
from django.http import FileResponse, Http404, JsonResponse
from django.template.loader import render_to_string
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import NotificationEvent as Event
from core.models import user_is_hr
from core.email_cases import ATTACH_CERTIFICATE, resolve
from core.notify import absolute, notify, render_email
from core.people import search_people

from .models import (AwardCycle, Certification, Nomination, Praise,
                     TaqdeerSettings)

QUARTERS = [(1, 'Q1'), (2, 'Q2'), (3, 'Q3'), (4, 'Q4')]


def _is_manager(user):
    """Somebody with at least one direct report in the employee tree."""
    try:
        employee = user.employee
    except Exception:                                          # noqa: BLE001
        return False
    return employee is not None and employee.direct_reports.exists()


def can_nominate(user):
    return bool(user.is_superuser or user_is_hr(user) or _is_manager(user))


def _hr_only(request):
    if not user_is_hr(request.user):
        raise PermissionDenied


def _visible_praise():
    """Delegates, so the page and the profile cannot disagree about what is
    publishable once approval is switched on."""
    from .employee_views import visible_praise
    return visible_praise()


#: How many cards a page of the wall holds.
FEED_PAGE = 30


def _feed_page(user, offset=0, limit=None):
    """(cards, more_remain, next_offset) for one page of the merged feed.

    Kudos and certifications are two tables and one wall, interleaved by date.
    The page used to take forty of one and twelve of the other, merge, and cut
    the result at thirty — so the thirty-first thing anybody was thanked for was
    not on a second page, it was nowhere: there was no second page, and the feed
    silently ended.

    Both sides are read `offset + limit + 1` deep. That is enough to be certain:
    each source comes back newest-first, so the first n of the merge cannot
    contain anything outside the first n of either source. The spare one is how
    we know whether to offer more without counting the whole of both tables.
    """
    # Read at call time, not bound as a default: a default argument is
    # evaluated once when the function is defined, so FEED_PAGE could never be
    # changed afterwards — including by a test trying to exercise a second page
    # without inventing thirty Kudos.
    limit = limit or FEED_PAGE
    need = offset + limit + 1
    rows = list(_visible_praise()[:need])
    s = TaqdeerSettings.get()
    if s.certification_on_taqdeer:
        rows += list(Certification.objects
                     .filter(published=True)
                     .select_related('employee', 'employee__employee')[:need])
    rows.sort(key=lambda i: i.created_at, reverse=True)

    from .feed_views import annotate
    page = rows[offset:offset + limit]
    annotate(page, user)
    return page, len(rows) > offset + limit, offset + len(page)


# ── the wall ────────────────────────────────────────────────────────────────

@login_required
def home(request):
    """AJi Taqdeer: the Kudos feed, and this quarter's Athar recipients."""
    praise = list(_visible_praise()[:40])

    # Results that are still inside their visibility window lead the page; the
    # rest are reachable from the archive rather than gone.
    from .employee_views import current_results
    live = list(current_results(s0) if (s0 := TaqdeerSettings.get()) else [])
    issued = (Nomination.objects
              .filter(status=Nomination.Status.APPROVED)
              .select_related('cycle', 'recipient', 'recipient__employee',
                              'nominated_by')
              .order_by('-cycle__year', '-cycle__quarter', 'recipient_name'))
    latest_cycle = live[0] if live else (issued[0].cycle if issued else None)
    winners = [n for n in issued if latest_cycle and n.cycle_id == latest_cycle.pk]

    # Who has been thanked most this quarter. Recipients, deliberately not a
    # public ranking of people — it is capped at five and framed as "recently
    # recognised" rather than a scoreboard.
    since = timezone.now() - timezone.timedelta(days=90)
    recent = (User.objects
              .filter(praise_received__hidden_at__isnull=True,
                      praise_received__created_at__gte=since)
              .annotate(n=Count('praise_received'))
              .select_related('employee')
              .order_by('-n')[:5])

    # One feed. A praise and a certification are the same social object —
    # something good happened to somebody — so they are interleaved by date
    # rather than kept in separate columns where the rail one gets missed.
    s = TaqdeerSettings.get()
    feed, feed_has_more, feed_next = _feed_page(request.user)
    certs = [i for i in feed if not hasattr(i, 'subject')]

    # Whether the rail has anything in it. Both of its boxes were removed and
    # the rest are conditional, so on most days it was an empty third of the
    # page holding the feed to two columns.
    open_cycle = AwardCycle.current()
    rail = bool(open_cycle and can_nominate(request.user))

    return render(request, 'taqdeer/home.html', {
        'rail': rail,
        'feed': feed,
        'certs': certs,
        'settings': s,
        'praise': praise,
        'winners': winners,
        'latest_cycle': latest_cycle,
        'recent': recent,
        'n_praise': _visible_praise().count(),
        'live_cycle': live[0] if live else None,
        'n_certs': Certification.objects.filter(published=True).count(),
        'n_awards': len(issued),
        'open_cycle': open_cycle,
        'can_nominate': can_nominate(request.user),
        'is_hr': user_is_hr(request.user),
        'feed_has_more': feed_has_more,
        'feed_next': feed_next,
    })


@login_required
def feed_more(request):
    """The next page of the wall, as cards, for the "Show older" button.

    Answers with the markup rather than JSON rows: the card is a template and
    building it a second time in JavaScript is how two versions of a card start
    to drift apart.
    """
    try:
        offset = max(0, int(request.GET.get('offset') or 0))
    except (TypeError, ValueError):
        offset = 0
    feed, more, nxt = _feed_page(request.user, offset)
    # The same context the page gives the include, or the cards that arrive
    # come back without their controls: `is_hr` undefined is falsy, so HR's Hide
    # quietly disappeared from everything below the first thirty.
    html = render_to_string('taqdeer/_feed_cards.html',
                            {'feed': feed, 'is_hr': user_is_hr(request.user)},
                            request=request)
    return JsonResponse({'html': html, 'more': more, 'next': nxt})


@login_required
@require_POST
def praise_new(request):
    """Post one Kudos. No approval step — the giver's name is the check."""
    who = request.POST.get('recipient') or ''
    subject = (request.POST.get('subject') or '').strip()
    message = (request.POST.get('message') or '').strip()

    recipient = User.objects.filter(pk=who).first() if who.isdigit() else None
    s = TaqdeerSettings.get()

    # Who may give one at all. Empty means everybody, which is the usual answer.
    allowed = list(s.praise_givers.values_list('pk', flat=True))
    if allowed and not (request.user.is_superuser or user_is_hr(request.user)
                        or request.user.pk in allowed):
        raise PermissionDenied

    if recipient is None:
        messages.error(request, 'Choose a colleague to thank.')
    elif recipient.pk == request.user.pk:
        messages.error(request, 'Kudos goes to somebody else.')
    elif not subject or not message:
        messages.error(request, 'Kudos needs a subject and a few words.')
    else:
        pending = s.praise_needs_approval
        praise = Praise.objects.create(
            giver=request.user, recipient=recipient,
            subject=subject, message=message,
            category=(request.POST.get('category') or '').strip()[:60],
            project=(request.POST.get('project') or '').strip()[:160],
            attachment=(request.FILES.get('attachment')
                        if s.praise_allow_attachment else None),
            status=Praise.Status.PENDING if pending else Praise.Status.APPROVED)
        giver = request.user.get_full_name() or request.user.username
        who_to = recipient.get_full_name() or recipient.username

        if pending:
            # The recipient is not told until it is published, or the surprise
            # is spoiled by something that may never appear.
            for hr in User.objects.filter(groups__name='HR', is_active=True).distinct():
                notify(hr, Event.APPROVAL,
                       title=f'Kudos from {giver} is waiting',
                       body=f'For {who_to}: {subject}',
                       link=reverse('taqdeer_praise_queue'), actor=request.user,
                       case='kudos.awaiting_moderation',
                       values={'actor': giver, 'recipient': who_to,
                               'subject_line': subject,
                               'name': hr.get_full_name() or hr.username})
            messages.success(request, f'Sent to HR. {who_to} will see it once it '
                                      f'is published.')
        else:
            notify(recipient, Event.GENERAL,
                   title=f'{giver} sent you Kudos',
                   body=subject, link=reverse('taqdeer_home'), actor=request.user,
                   case='kudos.received',
                   values={'actor': giver, 'subject_line': subject,
                           'name': who_to})
            messages.success(request, f'Kudos sent to {who_to}.')
    return redirect('taqdeer_home')


@login_required
@require_POST
def praise_hide(request, pk):
    """Taken down. The row stays; it stops rendering.

    Two people can do this and they are doing different things. HR moderates:
    something was said that should not stand. The giver withdraws: they wrote it
    and have changed their mind, or got the wrong person. Same effect on the
    record, so the same view, but the wording follows who pressed it — telling
    somebody their own Kudos has been "hidden" reads like a reprimand.

    Nobody else, and the recipient least of all: a Kudos is not theirs to
    remove, and letting them take one down turns a thank-you into something they
    have to accept or reject.
    """
    p = get_object_or_404(Praise, pk=pk)
    mine = p.giver_id == request.user.pk
    if not (mine or user_is_hr(request.user)):
        raise PermissionDenied
    p.hidden_at = timezone.now()
    p.hidden_by = request.user
    p.save(update_fields=['hidden_at', 'hidden_by'])
    messages.success(request, 'Kudos withdrawn.' if mine else 'Kudos hidden.')
    return redirect(request.POST.get('next') or 'taqdeer_home')


# ── HR ──────────────────────────────────────────────────────────────────────

@login_required
def manage(request):
    """Everything HR needs for the award, on one screen."""
    _hr_only(request)
    cycles = (AwardCycle.objects.annotate(n=Count('nominations'))
              .order_by('-year', '-quarter'))
    cycle_pk = request.GET.get('cycle')
    cycle = (AwardCycle.objects.filter(pk=cycle_pk).first() if cycle_pk
             else (AwardCycle.current() or cycles.first()))

    noms = []
    if cycle:
        noms = list(cycle.nominations
                    .select_related('recipient', 'nominated_by',
                                    'recipient__employee')
                    .order_by('status', 'recipient_name'))

    hidden = (Praise.objects.filter(hidden_at__isnull=False)
              .select_related('giver', 'recipient', 'hidden_by')[:20])

    return render(request, 'taqdeer/manage.html', {
        'cycles': cycles,
        'cycle': cycle,
        'noms': noms,
        'quarters': QUARTERS,
        'this_year': timezone.now().year,
        'hidden': hidden,
        'approved': [n for n in noms if n.is_approved],
        'is_hr': True,
    })


@login_required
@require_POST
def nomination_update(request, pk):
    """HR correcting, approving or returning one nomination.

    Approval is what generates the certificate — so a name corrected after
    approval regenerates it rather than leaving the old PDF on disk.
    """
    _hr_only(request)
    n = get_object_or_404(Nomination, pk=pk)
    action = request.POST.get('action')
    # The settings page promises "untick one and it stops". Two of these
    # messages went out whatever HR had chosen, because nothing ever read the
    # switch that governs them.
    s = TaqdeerSettings.get()

    if action in ('save', 'approve'):
        n.recipient_name = (request.POST.get('recipient_name') or n.recipient_name).strip()
        n.citation = (request.POST.get('citation') or n.citation).strip()

    if action == 'approve':
        n.status = Nomination.Status.APPROVED
        n.reviewed_by = request.user
        n.reviewed_at = timezone.now()
        n.save()
        _build_certificate(n)
        notify(n.recipient, Event.GENERAL,
               title='You have received The Athar Award',
               body=f'{n.cycle.label}. {n.citation[:180]}',
               link=reverse('taqdeer_home'), actor=request.user,
               case='athar.nomination_approved_winner',
               values={'name': n.recipient_name, 'period': n.cycle.label,
                       'citation': n.citation})
        if s.notify_approved:
            notify(n.nominated_by, Event.APPROVAL,
                   title=f'Your nomination for {n.recipient_name} was approved',
                   body=n.cycle.label, link=reverse('taqdeer_home'),
                   actor=request.user,
                   case='athar.nomination_approved_manager',
                   values={'nominee': n.recipient_name,
                           'period': n.cycle.label,
                           'name': (n.nominated_by.get_full_name()
                                    or n.nominated_by.username)})
        messages.success(request, f'Approved. Certificate generated for {n.recipient_name}.')

    elif action == 'return':
        n.status = Nomination.Status.RETURNED
        n.hr_note = (request.POST.get('hr_note') or '').strip()
        n.reviewed_by = request.user
        n.reviewed_at = timezone.now()
        n.save()
        if s.notify_returned:
            notify(n.nominated_by, Event.APPROVAL,
                   title=f'Your nomination for {n.recipient_name} needs a change',
                   body=n.hr_note or 'HR has sent it back.',
                   link=reverse('taqdeer_submissions'), actor=request.user,
                   case='athar.nomination_returned',
                   values={'nominee': n.recipient_name,
                           'hr_note': n.hr_note or 'HR has sent it back.',
                           'period': n.cycle.label,
                           'name': (n.nominated_by.get_full_name()
                                    or n.nominated_by.username)})
        messages.success(request, 'Sent back to the manager.')

    elif action == 'save':
        if n.is_approved:
            n.save()
            _build_certificate(n)         # the printed name changed
            messages.success(request, 'Saved, and the certificate regenerated.')
        else:
            n.save()
            messages.success(request, 'Saved.')

    elif action == 'delete':
        name = n.recipient_name
        n.delete()
        messages.success(request, f'Nomination for {name} removed.')
        return redirect(f'{reverse("taqdeer_manage")}?cycle={request.POST.get("cycle") or ""}')

    return redirect(f'{reverse("taqdeer_manage")}?cycle={n.cycle_id}')


def _build_certificate(n):
    """Render and attach the PDF, using whatever HR has configured.

    Was reading two GlobalSetting keys and defaulting to the HR Manager. The
    specification asks for the CEO and for the wording to be editable, both of
    which now live in TaqdeerSettings — so this delegates rather than keeping a
    second, staler idea of what a certificate says.
    """
    from .release_views import build_certificate
    build_certificate(n)


@login_required
@require_POST
def cycle_action(request):
    """Open a quarter, move it to review, or issue it."""
    _hr_only(request)
    action = request.POST.get('action')

    if action == 'open':
        year = int(request.POST.get('year') or timezone.now().year)
        quarter = int(request.POST.get('quarter') or 1)
        AwardCycle.objects.filter(stage=AwardCycle.Stage.NOMINATING).update(
            stage=AwardCycle.Stage.REVIEW)
        cycle, created = AwardCycle.objects.get_or_create(year=year, quarter=quarter)
        cycle.stage = AwardCycle.Stage.NOMINATING
        cycle.note = (request.POST.get('note') or '').strip()
        cycle.save()
        messages.success(request, f'{cycle.label} is open for nominations.')
        return redirect(f'{reverse("taqdeer_manage")}?cycle={cycle.pk}')

    cycle = get_object_or_404(AwardCycle, pk=request.POST.get('cycle'))

    if action == 'review':
        cycle.stage = AwardCycle.Stage.REVIEW
        cycle.save(update_fields=['stage'])
        messages.success(request, f'{cycle.label} closed to new nominations.')

    elif action == 'issue':
        # Releasing is taqdeer/release_views.release now: it states the
        # consequence in numbers first, and refuses to run a second time.
        # Leaving this here as a live path meant one POST could email the whole
        # quarter from a review screen with no confirmation at all.
        messages.error(request, 'Releasing a quarter happens on its own page.')
        return redirect('taqdeer_release', pk=cycle.pk)

    return redirect(f'{reverse("taqdeer_manage")}?cycle={cycle.pk}')


def _email_recipients(nominations, cycle):
    """The official email, to the recipient, copying nobody else.

    Deliberately not an all-staff announcement: the page is the announcement
    channel. A thousand-person mail per individual award is how a recognition
    programme gets switched off.
    """
    from core.mailer import send

    sent = failed = 0
    for n in nominations:
        address = (n.recipient.email or '').strip()
        if not address:
            failed += 1
            continue
        # The same case the release path uses, so there is one wording for
        # "you have won" rather than two that drift apart. It also settles an
        # old lie: this email used to say the certificate was attached, and
        # nothing was ever attached to it, because send() had no way to.
        s_ = TaqdeerSettings.get()
        shape = resolve('athar.released', {
            'name': n.recipient_name,
            'award_title': s_.certificate_title,
            'period': cycle.label,
            'congratulations': s_.congratulations_message,
            'recognition_line': s_.certificate_recognition_line,
            'citation': n.citation,
        })
        if shape is None or not shape.enabled:
            continue
        files = None
        if shape.attachment == ATTACH_CERTIFICATE:
            from .release_views import _certificate_attachment
            files = _certificate_attachment(n)
        html = render_email(
            title=shape.subject, lines=shape.body_lines,
            link=absolute(reverse('taqdeer_certificate', args=[n.pk])),
            link_label=shape.link_label, footer=shape.footer)
        ok, _ = send(address, shape.subject, html, attachments=files,
                     high_importance=shape.high_importance)
        if ok:
            n.emailed_at = timezone.now()
            n.save(update_fields=['emailed_at'])
            sent += 1
        else:
            failed += 1
    return sent, failed


@login_required
def certificate(request, pk):
    n = get_object_or_404(Nomination, pk=pk)
    if not n.certificate:
        raise Http404('No certificate has been generated yet.')
    allowed = (user_is_hr(request.user) or request.user.is_superuser
               or request.user.pk in (n.recipient_id, n.nominated_by_id))
    if not allowed:
        raise PermissionDenied
    return FileResponse(n.certificate.open('rb'), as_attachment=True,
                        filename=n.certificate.name.rsplit('/', 1)[-1])


@login_required
def people(request):
    """Picker search. Everyone may thank anyone, so the pool is everyone."""
    return JsonResponse({'users': search_people(request.GET.get('q', ''),
                                                exclude=(request.user,))})
