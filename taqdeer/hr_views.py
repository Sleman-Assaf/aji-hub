"""The HR side of AJi Taqdeer: running a quarter, and configuring the module.

Kept apart from views.py, which is the employee and manager side. The two have
different readers and different permissions, and one file that mixes "post an
Kudos" with "release a quarter" invites the wrong decorator on the wrong view.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.utils import timezone

from core.models import ITAuditLog
from core.models import NotificationEvent as Event
from core.models import user_is_hr
from core.notify import notify

from .models import AwardCycle, Nomination, TaqdeerSettings
from .views import _hr_only, _is_manager


def _taqdeer_admins():
    """Everyone who can run Taqdeer, for the notifications that are theirs.

    `user_is_hr` answers this one account at a time and accepts three different
    ways of being HR — the profile role, the HR group, or a superuser flag — so
    there is no queryset to filter on. Asking it per active account is the only
    answer that agrees with the permission the pages actually enforce.
    """
    return [u for u in User.objects.filter(is_active=True).select_related('profile')
            if user_is_hr(u)]


def _tell_admins_of_draft(request, cycle):
    """A quarter has been drafted. The other administrators should know.

    Deliberately not the nominators: a draft announces nothing to staff, which is
    what the create button promises, and a notification is an announcement. The
    creator is skipped too — `notify` drops anything whose actor is its
    recipient — so this is only ever news to somebody.
    """
    if not TaqdeerSettings.get().notify_period_draft:
        return 0
    window = ''
    if cycle.opens_on and cycle.closes_on:
        window = (f' Nominations would run {cycle.opens_on:%d %b} to '
                  f'{cycle.closes_on:%d %b}.')
    who = request.user.get_full_name() or request.user.username
    sent = 0
    for admin in _taqdeer_admins():
        out = notify(
            admin, Event.GENERAL,
            title=f'{cycle.label} Athar period drafted',
            body=f'{who} created it as a draft, so nothing has been sent to '
                 f'anybody yet.{window} Open it when the dates are right.',
            link=reverse('taqdeer_athar_period', args=[cycle.pk]),
            actor=request.user,
            case='athar.period_drafted',
            values={'period': cycle.label, 'actor': who,
                    'name': admin.get_full_name() or admin.username})
        sent += bool(out['in_app'])
    return sent


def _period_stats(cycle):
    """The KPIs the specification asks for, from one pass over the rows."""
    noms = list(cycle.nominations.all())
    submitters = {n.nominated_by_id for n in noms}
    return {
        'total': len(noms),
        'approved': sum(1 for n in noms if n.status == Nomination.Status.APPROVED),
        'pending': sum(1 for n in noms if n.status == Nomination.Status.SUBMITTED),
        'returned': sum(1 for n in noms if n.status == Nomination.Status.RETURNED),
        'rejected': sum(1 for n in noms if n.status == Nomination.Status.REJECTED),
        'submitters': len(submitters),
    }


def _next_free_quarter():
    """The first (year, quarter) that has no period yet, from this one onward.

    The form used to open on the *current* quarter. Once that quarter existed —
    which it does as soon as anybody runs one — pressing Create as draft failed
    on the year/quarter uniqueness rule, and the failure was a form-wide error
    the page did not print. The button read as broken when it was in fact
    refusing to create a duplicate.

    A period is created for the quarter about to start at least as often as for
    the one running, so offering the next free slot is also the better default.
    """
    now = timezone.now()
    year, quarter = now.year, (now.month - 1) // 3 + 1
    taken = set(AwardCycle.objects.values_list('year', 'quarter'))
    # Bounded rather than `while True`: eight quarters is two years, and if all
    # of them are taken the honest thing is to hand back the current one and let
    # the form say so.
    for _ in range(8):
        if (year, quarter) not in taken:
            return year, quarter
        quarter += 1
        if quarter > 4:
            year, quarter = year + 1, 1
    return now.year, (now.month - 1) // 3 + 1


@login_required
def athar_manage(request):
    """Every quarter, its state, and how it is going."""
    _hr_only(request)
    from .forms import PeriodForm

    if request.method == 'POST':
        form = PeriodForm(request.POST)
        if form.is_valid():
            cycle = form.save(commit=False)
            cycle.stage = AwardCycle.Stage.DRAFT
            cycle.save()
            form.save_m2m()
            assigned = _tell_newly_assigned(
                request, cycle, set(cycle.authorised_managers.all()))
            told = _tell_admins_of_draft(request, cycle)
            messages.success(
                request,
                f'{cycle.label} created as a draft.'
                + _assignment_note(assigned)
                + (f' {told} administrator{"" if told == 1 else "s"} notified.'
                   if told else ''))
            return redirect('taqdeer_athar_period', pk=cycle.pk)
        messages.error(request, 'That period could not be created — see the form.')
    else:
        s = TaqdeerSettings.get()
        year, quarter = _next_free_quarter()
        form = PeriodForm(initial={
            'year': year, 'quarter': quarter,
            'awards_per_manager': s.default_awards_per_manager})

    rows = [(c, _period_stats(c)) for c in AwardCycle.objects.all()]
    return render(request, 'taqdeer/athar_manage.html', {
        'rows': rows, 'form': form, 'is_hr': True,
        'open_cycle': AwardCycle.current(),
    })


#: What each stage may become. A period cannot skip review, and it cannot
#: reopen after release — the button that would do it is simply not offered,
#: which is a better guard than a confirmation nobody reads.
NEXT_STAGE = {
    AwardCycle.Stage.DRAFT: [
        ('open', AwardCycle.Stage.NOMINATING, 'Open for nominations')],
    AwardCycle.Stage.NOMINATING: [
        ('close', AwardCycle.Stage.SUBMISSIONS_CLOSED, 'Close submissions')],
    AwardCycle.Stage.SUBMISSIONS_CLOSED: [
        ('review', AwardCycle.Stage.REVIEW, 'Start HR review')],
    AwardCycle.Stage.REVIEW: [
        ('ready', AwardCycle.Stage.APPROVED, 'Mark ready to release')],
    #: Release itself is phase four; nothing here performs it.
    AwardCycle.Stage.APPROVED: [],
    AwardCycle.Stage.ISSUED: [
        ('archive', AwardCycle.Stage.CLOSED, 'Archive')],
    AwardCycle.Stage.CLOSED: [],
}

#: And back again. Every forward step is reversible, because the alternative HR
#: had was to delete the quarter and rebuild it — which loses the nominations
#: with it. Reopening for nominations goes through the same one-quarter-at-a-time
#: guard as opening does, since it lands on the same stage.
#:
#: Undoing a release is the one step that cannot fully undo itself: the
#: certificates were generated and the emails were sent, and moving the stage
#: back does not unsend them. The action says so rather than pretending.
PREV_STAGE = {
    AwardCycle.Stage.DRAFT: [],
    AwardCycle.Stage.NOMINATING: [
        ('back_draft', AwardCycle.Stage.DRAFT, 'Back to draft')],
    AwardCycle.Stage.SUBMISSIONS_CLOSED: [
        ('back_open', AwardCycle.Stage.NOMINATING, 'Reopen for nominations')],
    AwardCycle.Stage.REVIEW: [
        ('back_closed', AwardCycle.Stage.SUBMISSIONS_CLOSED, 'Back to submissions closed')],
    AwardCycle.Stage.APPROVED: [
        ('back_review', AwardCycle.Stage.REVIEW, 'Back to HR review')],
    AwardCycle.Stage.ISSUED: [
        ('back_approved', AwardCycle.Stage.APPROVED, 'Undo the release')],
    AwardCycle.Stage.CLOSED: [
        ('back_issued', AwardCycle.Stage.ISSUED, 'Back to released')],
}


@login_required
def athar_period(request, pk):
    """One quarter: what it is set to, how it is going, and what it may do next."""
    _hr_only(request)
    from .forms import PeriodForm
    cycle = get_object_or_404(AwardCycle, pk=pk)
    form = PeriodForm(instance=cycle)

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'save':
            # Who was named before the save, so only the additions are told. HR
            # editing a date on a quarter must not re-notify everybody already on
            # it, and saving the form twice must not notify anybody twice.
            before = set(cycle.authorised_managers.all())
            form = PeriodForm(request.POST, instance=cycle)
            if form.is_valid():
                form.save()
                added = set(cycle.authorised_managers.all()) - before
                told = _tell_newly_assigned(request, cycle, added)
                messages.success(request, 'Saved.' + _assignment_note(told))
                return redirect('taqdeer_athar_period', pk=cycle.pk)
            messages.error(request, 'Check the dates and try again.')
        elif _advance(request, cycle, action):
            return redirect('taqdeer_athar_period', pk=cycle.pk)

    return render(request, 'taqdeer/athar_period.html', {
        'cycle': cycle, 'form': form, 'stats': _period_stats(cycle),
        # Once nominations have closed, the setup is history rather than
        # something to fill in: the dates have passed and who was allowed to
        # nominate is a matter of record. The page shows it as a summary and
        # keeps the form behind "Edit period", so the screen HR actually needs
        # at that point — the nominations — is not underneath a form they have
        # already finished with.
        'setup_locked': cycle.stage not in (AwardCycle.Stage.DRAFT,
                                            AwardCycle.Stage.NOMINATING),
        'steps': NEXT_STAGE.get(cycle.stage, []),
        'back_steps': PREV_STAGE.get(cycle.stage, []),
        'noms': cycle.nominations.select_related(
            'recipient', 'nominated_by', 'recipient__employee'
        ).order_by('status', 'recipient_name'),
        'is_hr': True,
    })


def _advance(request, cycle, action):
    """Move a period one step in either direction, if that step is allowed.

    Forward and back share this because they share every consequence: landing on
    NOMINATING closes whatever else is open and notifies the managers, and it
    does that whether the period arrived from a draft or came back from being
    closed.
    """
    allowed = {a: (stage, label)
               for a, stage, label in (NEXT_STAGE.get(cycle.stage, [])
                                       + PREV_STAGE.get(cycle.stage, []))}
    if action not in allowed:
        messages.error(request, 'That is not a step this period can take now.')
        return False
    stage, label = allowed[action]
    going_back = action in {a for a, _, _ in PREV_STAGE.get(cycle.stage, [])}

    # Only one quarter takes nominations at a time. Two open windows and a
    # manager sees two forms, with an allocation that means nothing in either.
    if stage == AwardCycle.Stage.NOMINATING:
        others = list(AwardCycle.objects
                      .filter(stage=AwardCycle.Stage.NOMINATING)
                      .exclude(pk=cycle.pk))
        for other in others:
            other.stage = AwardCycle.Stage.SUBMISSIONS_CLOSED
            other.save(update_fields=['stage'])
        if others:
            messages.warning(
                request,
                f'{", ".join(o.label for o in others)} closed to new nominations '
                f'— only one quarter can be open at a time.')
        _tell_managers(request, cycle)

    was = cycle.get_stage_display()
    cycle.stage = stage
    cycle.save(update_fields=['stage'])
    if going_back:
        note = (' The certificates that were issued still exist and the emails '
                'have already gone out — this changes the stage, not those.'
                if was == 'Released' else '')
        messages.success(request,
                         f'{cycle.label} moved back: {label.lower()}.{note}')
    else:
        messages.success(request, f'{cycle.label}: {label.lower()}.')
    return True


@login_required
@require_POST
def athar_delete(request, pk):
    """Delete a quarter and everything submitted into it.

    Guarded by a confirmation that names the count rather than by refusing:
    whoever runs this knows their own data, and the thing they need is to be
    told what goes with it, not to be argued with. The nominations cascade, so
    a released quarter takes its winners out of the archive as well.
    """
    _hr_only(request)
    cycle = get_object_or_404(AwardCycle, pk=pk)
    label, stage = cycle.label, cycle.get_stage_display()
    n = cycle.nominations.count()
    # Read before the delete, written after it: the counts are gone once the
    # cascade has run, and a log entry for something that failed to delete would
    # be worse than none.
    cycle.delete()
    ITAuditLog.write(
        request, ITAuditLog.Action.CONTENT_DELETE,
        f'Deleted the {label} Athar period, stage {stage}'
        + (f' and the {n} nomination{"" if n == 1 else "s"} in it'
           if n else ' — it held no nominations'),
        target=label)
    messages.success(
        request,
        f'{label} deleted, stage {stage}'
        + (f', with {n} nomination{"" if n == 1 else "s"}.' if n else '.'))
    return redirect('taqdeer_athar_manage')


def eligible_submitters(cycle):
    """Who may nominate into this cycle.

    People, named. HR picks them from the directory rather than reasoning about
    which Django group somebody happens to be in — that indirection was the
    single thing they said made this page unusable.

    The order is: whoever this quarter names, else whoever Settings names, else
    everyone with a direct report. The last is a fallback for a period opened
    in a hurry, not a default anybody has to accept.
    """
    named = list(cycle.authorised_managers.filter(is_active=True))
    if named:
        return set(named)

    fallback = list(TaqdeerSettings.get().eligible_submitters.filter(is_active=True))
    if fallback:
        return set(fallback)

    return {u for u in User.objects.filter(is_active=True)
            .select_related('employee') if _is_manager(u)}


def _actor_for(request, person):
    """The actor to attribute a notification to, or None for the person acting.

    notify() drops anything whose actor is also its recipient. That is right for
    "X liked your post" and wrong for "you may now nominate": being handed
    something to do is not the same as being told about your own doing.

    It matters because HR running the quarter they also nominate in is the normal
    case here, not an edge. With one person in the HR group, attributing these to
    the actor meant the only person who needed the deadline and the link was the
    one guaranteed not to get them.
    """
    return None if person.pk == request.user.pk else request.user


def _tell_newly_assigned(request, cycle, people):
    """Tell the managers HR has just added to this quarter.

    Assignment and opening are two different moments and only one of them was
    covered. A manager added to a quarter that was already open heard nothing at
    all — the open notification had already gone out — so HR named them and
    nothing reached them. This closes that.

    The wording turns on whether they can act yet: a quarter that is taking
    nominations gets a deadline and a link that works now, and one that has not
    opened says so rather than sending somebody to a form that will refuse them.
    """
    if not people or not TaqdeerSettings.get().notify_manager_assigned:
        return 0
    n = cycle.awards_per_manager
    # `allowed` reads as a phrase on its own — "3 people" — because the wording
    # around it belongs to the case, where somebody may rewrite "nominate up to"
    # into something else entirely.
    allowed = f'{n} {"person" if n == 1 else "people"}'
    closing = (f'Nominations close {cycle.closes_on:%d %B}.' if cycle.closes_on
               else 'A closing date has not been set yet.')
    opening = (f'It opens {cycle.opens_on:%d %B}' if cycle.opens_on
               else 'HR will open it shortly')
    if cycle.stage == AwardCycle.Stage.NOMINATING:
        case_code = 'athar.manager_assigned_open'
        title = f'{cycle.label}: you can put people forward for the Athar Award'
        body = f'HR has asked you to nominate up to {allowed}. {closing}'
    else:
        case_code = 'athar.manager_assigned_upcoming'
        title = f'{cycle.label}: HR has asked you to nominate for the Athar Award'
        body = (f'You will be able to put forward up to {allowed}. {opening}, '
                f'and you will be told again then.')

    told = 0
    for person in people:
        notify(person, Event.APPROVAL, title=title, body=body,
               link=reverse('taqdeer_submissions'),
               actor=_actor_for(request, person),
               case=case_code,
               values={'name': person.get_full_name() or person.username,
                       'period': cycle.label, 'allowance': allowed,
                       'closing': closing, 'opening': opening,
                       'actor': request.user.get_full_name()
                                or request.user.username})
        told += 1
    return told


def _assignment_note(told):
    """The line added to HR's own confirmation, so they can see it happened."""
    if not told:
        return ''
    return (f' {told} manager{"" if told == 1 else "s"} '
            f'{"was" if told == 1 else "were"} told.')


def _tell_managers(request, cycle):
    """The opening notification: what is open, how many, and by when."""
    if not TaqdeerSettings.get().notify_period_open:
        return
    when = (f'Nominations close {cycle.closes_on:%d %B}.' if cycle.closes_on
            else 'A closing date has not been set yet.')
    n = cycle.awards_per_manager
    for person in eligible_submitters(cycle):
        notify(person, Event.APPROVAL,
               title=f'{cycle.label} Athar nominations are open',
               body=f'You may put forward up to {n} '
                    f'{"person" if n == 1 else "people"}. {when}',
               link=reverse('taqdeer_submissions'),
               actor=_actor_for(request, person),
               case='athar.period_open',
               values={'name': person.get_full_name() or person.username,
                       'period': cycle.label,
                       'allowance': f'{n} {"person" if n == 1 else "people"}',
                       'closing': when})


@login_required
def settings_page(request):
    """Everything configurable, in the five groups the specification names."""
    _hr_only(request)
    from .forms import SettingsForm
    obj = TaqdeerSettings.get()

    if request.method == 'POST':
        form = SettingsForm(request.POST, request.FILES, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, 'Settings saved.')
            return redirect('taqdeer_settings')
        messages.error(request, 'Some of that could not be saved — see below.')
    else:
        form = SettingsForm(instance=obj)

    return render(request, 'taqdeer/settings.html', {'form': form, 'is_hr': True})


@login_required
def people_all(request):
    """Directory search for the HR pickers.

    Nobody is excluded: HR may well want to name themselves as an eligible
    nominator, which the feed picker deliberately prevents for a praise.
    """
    from django.http import JsonResponse

    from core.people import search_people
    _hr_only(request)
    return JsonResponse({'users': search_people(request.GET.get('q', ''))})
