"""The manager side of the Athar Award: putting people forward, and tracking them.

Separate from views.py (employee) and hr_views.py (HR) because the three have
different readers and different rules. This module's whole job is one question
asked well — who deserves this, and why — plus honest status on what happened
to the answers.

Two rules are enforced here rather than in the template, because a template
that hides a button is a suggestion and this is an allocation:

  eligibility  whether this person may nominate into this cycle at all
  allocation   how many they have left, checked again on submit
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import NotificationEvent as Event
from core.models import user_is_hr
from core.notify import notify
from core.people import search_people

from .hr_views import eligible_submitters
from .models import AwardCycle, Nomination, TaqdeerSettings


def _may_submit(user, cycle):
    """Whether this person may nominate into this cycle.

    HR and superusers always may — somebody has to be able to test the form.
    Everyone else has to be in the cycle's resolved eligibility.
    """
    if user.is_superuser or user_is_hr(user):
        return True
    return user in eligible_submitters(cycle)


def _allocation(user, cycle):
    """How many awards this manager has used, and has left, in this cycle.

    A returned nomination still counts as used: it is theirs to fix, not a
    free slot. A rejected one does not — HR has taken it off the table.
    """
    used = cycle.nominations.filter(nominated_by=user).exclude(
        status=Nomination.Status.REJECTED).count()
    total = cycle.awards_per_manager
    return {'used': used, 'total': total, 'remaining': max(0, total - used),
            'exhausted': used >= total}


def _may_receive(cycle, person):
    """Whether this person may be nominated in this cycle."""
    groups = list(cycle.recipient_groups.all())
    if not groups:
        return True
    return person.groups.filter(pk__in=[g.pk for g in groups]).exists()


@login_required
def submissions(request):
    """My Athar Submissions: the open period, what is left, and what happened.

    This is the manager's whole view of the award. The create form lives on it
    rather than behind a separate page, because a form that needs a page load
    to reach is a form that gets put off.
    """
    cycle = AwardCycle.current()
    eligible = bool(cycle) and _may_submit(request.user, cycle)
    alloc = _allocation(request.user, cycle) if cycle else None

    mine = (Nomination.objects
            .filter(nominated_by=request.user)
            .select_related('cycle', 'recipient', 'recipient__employee', 'reviewed_by')
            .order_by('-cycle__year', '-cycle__quarter', 'recipient_name'))

    returned = [n for n in mine if n.status == Nomination.Status.RETURNED]

    return render(request, 'taqdeer/submissions.html', {
        'cycle': cycle,
        'eligible': eligible,
        'alloc': alloc,
        'mine': mine,
        'this_period': [n for n in mine if cycle and n.cycle_id == cycle.pk],
        'earlier': [n for n in mine if not cycle or n.cycle_id != cycle.pk],
        'returned': returned,
        'is_hr': user_is_hr(request.user),
    })


@login_required
@require_POST
def nominate(request):
    """Submit one nomination.

    The allocation is checked here and not only on the page that drew the form:
    a manager with the form open in two tabs would otherwise submit past their
    limit, and the limit is the point.
    """
    cycle = AwardCycle.current()
    if cycle is None:
        messages.error(request, 'Nominations are not open at the moment.')
        return redirect('taqdeer_submissions')
    if not _may_submit(request.user, cycle):
        raise PermissionDenied

    alloc = _allocation(request.user, cycle)
    if alloc['exhausted']:
        messages.error(
            request,
            f'You have used all {alloc["total"]} of your {cycle.label} awards. '
            f'Withdraw one to put somebody else forward.')
        return redirect('taqdeer_submissions')

    chosen = request.POST.getlist('recipient')
    who = chosen[0] if chosen else ''
    reason = (request.POST.get('citation') or '').strip()
    person = User.objects.filter(pk=who).first() if who.isdigit() else None

    if person is None:
        messages.error(request, 'Choose the colleague you are nominating.')
    elif person.pk == request.user.pk:
        messages.error(request, 'An Athar award goes to somebody else.')
    elif not reason:
        messages.error(request, 'Say in one line why they deserve it.')
    elif not _may_receive(cycle, person):
        messages.error(
            request,
            f'{person.get_full_name() or person.username} is not eligible for '
            f'{cycle.label}. HR sets who can receive the award.')
    elif cycle.nominations.filter(recipient=person).exists():
        messages.warning(
            request,
            f'{person.get_full_name() or person.username} has already been '
            f'nominated for {cycle.label}.')
    else:
        Nomination.objects.create(
            cycle=cycle, nominated_by=request.user, recipient=person,
            recipient_name=person.get_full_name() or person.username,
            citation=reason, status=Nomination.Status.SUBMITTED)
        left = alloc['remaining'] - 1
        messages.success(
            request,
            f'{person.get_full_name() or person.username} put forward for '
            f'{cycle.label}. {left} award{"" if left == 1 else "s"} left.')
        _tell_hr(request, cycle, person)

    return redirect('taqdeer_submissions')


def _reviewers():
    """Everybody who can actually open the review page.

    Was `groups__name='HR'` alone, which is narrower than the page's own guard:
    _hr_only accepts a superuser and a profile HR flag as well, so an
    administrator could review nominations and was never told any had arrived.
    Notifying a list that does not match the list of people allowed to act is
    how a queue sits untouched.
    """
    return [u for u in User.objects.filter(is_active=True).distinct()
            if user_is_hr(u)]


def _tell_hr(request, cycle, person):
    """Tell the reviewers, and give the submitter a receipt.

    Both halves matter and only the first existed. notify() drops anything whose
    actor is also its recipient — right in general, nobody needs telling about
    their own doing — but it meant that when a reviewer submitted a nomination
    the only message the system tried to send was one to themselves, which was
    discarded. With one person in the HR group that is every notification for
    that submission, so HR nominated somebody and the Hub went completely quiet.

    The receipt fixes it properly rather than by disabling the actor rule: the
    submitter hears because their submission was received, which is a different
    fact from "somebody nominated somebody" and is worth its own line whoever
    they are.
    """
    if not TaqdeerSettings.get().notify_submission:
        return
    from_who = request.user.get_full_name() or request.user.username
    to_who = person.get_full_name() or person.username

    for hr in _reviewers():
        if hr.pk == request.user.pk:
            continue                       # they get the receipt below instead
        notify(hr, Event.APPROVAL,
               title=f'Athar nomination from {from_who}',
               body=f'{to_who} for {cycle.label}.',
               link=reverse('taqdeer_manage'), actor=request.user,
               case='athar.nomination_received',
               values={'actor': from_who, 'nominee': to_who,
                       'period': cycle.label,
                       'name': hr.get_full_name() or hr.username})

    # No actor: this one is *for* the person who acted, so the rule that drops
    # self-directed notifications must not apply to it.
    notify(request.user, Event.APPROVAL,
           title=f'Your {cycle.label} nomination is with HR',
           body=f'You put {to_who} forward. HR reviews it before anything is '
                f'announced, and you will be told when it is decided.',
           link=reverse('taqdeer_submissions'),
           case='athar.nomination_receipt',
           values={'nominee': to_who, 'period': cycle.label, 'name': from_who})


@login_required
@require_POST
def resubmit(request, pk):
    """Fix a nomination HR sent back, and send it again.

    Only the manager who made it, and only while it is returned — an approved
    one is HR's now, and a stranger's is nobody's business.
    """
    n = get_object_or_404(Nomination, pk=pk)
    if n.nominated_by_id != request.user.pk:
        raise PermissionDenied
    if n.status != Nomination.Status.RETURNED:
        messages.error(request, 'That nomination is not waiting on you.')
        return redirect('taqdeer_submissions')

    action = request.POST.get('action')
    if action == 'withdraw':
        name = n.recipient_name
        n.delete()
        messages.success(request, f'Nomination for {name} withdrawn.')
        return redirect('taqdeer_submissions')

    reason = (request.POST.get('citation') or '').strip()
    if not reason:
        messages.error(request, 'The reason cannot be empty.')
        return redirect('taqdeer_submissions')

    n.citation = reason
    n.status = Nomination.Status.SUBMITTED
    n.hr_note = ''
    n.save(update_fields=['citation', 'status', 'hr_note', 'updated_at'])
    messages.success(request, f'{n.recipient_name} sent back to HR.')
    _tell_hr(request, n.cycle, n.recipient)
    return redirect('taqdeer_submissions')


@login_required
def person_card(request):
    """Who this is, for the card the form shows once somebody is chosen.

    The spec asks for the name, department, position and photograph to appear
    after selection. Fetched rather than embedded: the picker searches 1,256
    people and shipping all of their details to draw one card would be absurd.
    """
    person = User.objects.filter(pk=request.GET.get('id')).select_related(
        'employee', 'employee__department').first()
    if person is None:
        return JsonResponse({'found': False})

    emp = getattr(person, 'employee', None)
    cycle = AwardCycle.current()
    return JsonResponse({
        'found': True,
        'name': (emp.full_name if emp else None) or person.get_full_name() or person.username,
        'position': (getattr(emp, 'role', '') or '') if emp else '',
        'department': (emp.department.name if emp and emp.department_id else ''),
        'photo': (emp.photo.url if emp and emp.photo else ''),
        'initials': ''.join(p[0] for p in ((emp.full_name if emp else person.get_full_name())
                                           or person.username).split()[:2]).upper(),
        'eligible': bool(cycle) and _may_receive(cycle, person),
        'already': bool(cycle) and cycle.nominations.filter(recipient=person).exists(),
    })


@login_required
def people(request):
    """The picker's pool: everyone but yourself."""
    return JsonResponse({'users': search_people(request.GET.get('q', ''),
                                                exclude=(request.user,))})
