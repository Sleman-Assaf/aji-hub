"""The employee-facing pages: my own recognition, and the archive.

Two readers, two questions. "What have I been recognised for" is personal and
private-ish — it gathers a person's whole history including the certificate only
they can open. "Who has won this before" is public and historical, and exists
because results leave the front page after a while and should not become
unreachable when they do.
"""
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from core.models import user_is_hr

from .models import AwardCycle, Certification, Nomination, Praise, TaqdeerSettings


def results_window(settings=None):
    """The date before which released results drop off the main page.

    Returns None when results are set to stay permanently, which the caller
    reads as "no cut-off" rather than "everything is expired" — the difference
    matters and getting it backwards would hide every result on the site.
    """
    s = settings or TaqdeerSettings.get()
    days = s.visibility_days
    if not days:
        return None
    return timezone.now() - timezone.timedelta(days=days)


def current_results(settings=None):
    """Released cycles still inside their visibility window, newest first."""
    cut = results_window(settings)
    qs = AwardCycle.objects.filter(stage=AwardCycle.Stage.ISSUED)
    if cut is not None:
        qs = qs.filter(issued_at__gte=cut)
    return qs.order_by('-year', '-quarter')


def visible_praise():
    """Praise that may be shown: not hidden, and approved if approval is on."""
    qs = Praise.objects.filter(hidden_at__isnull=True)
    if TaqdeerSettings.get().praise_needs_approval:
        qs = qs.filter(status=Praise.Status.APPROVED)
    return qs.select_related('giver', 'recipient',
                             'giver__employee', 'recipient__employee')


@login_required
def my_recognition(request):
    """Everything this person has been recognised for, and everything they gave.

    The one place the certificate is reachable, because the results feed
    deliberately does not carry it.
    """
    me = request.user
    s = TaqdeerSettings.get()

    received = list(visible_praise().filter(recipient=me))
    given = list(visible_praise().filter(giver=me))
    awards = list(Nomination.objects
                  .filter(recipient=me, status=Nomination.Status.APPROVED)
                  .select_related('cycle', 'nominated_by')
                  .order_by('-cycle__year', '-cycle__quarter'))
    certs = list(Certification.objects.filter(employee=me, published=True))

    # Praise this person gave that is still waiting, so they know why it has
    # not appeared rather than assuming it was lost.
    waiting = []
    if s.praise_needs_approval:
        waiting = list(Praise.objects.filter(
            giver=me, status=Praise.Status.PENDING, hidden_at__isnull=True)
            .select_related('recipient'))

    return render(request, 'taqdeer/my_recognition.html', {
        'received': received, 'given': given, 'awards': awards,
        'certs': certs, 'waiting': waiting,
        'total': len(received) + len(awards) + len(certs),
        'is_hr': user_is_hr(request.user),
    })


@login_required
def archive(request):
    """Every Athar quarter that has been released, however long ago.

    Results leave the front page after the configured window; they do not leave
    the record. Without this page the window would be a delete button with a
    timer on it.
    """
    cycles = (AwardCycle.objects
              .filter(stage__in=[AwardCycle.Stage.ISSUED, AwardCycle.Stage.CLOSED])
              .order_by('-year', '-quarter'))
    rows = []
    for c in cycles:
        rows.append((c, list(c.nominations
                             .filter(status=Nomination.Status.APPROVED)
                             .select_related('recipient', 'recipient__employee',
                                             'nominated_by')
                             .order_by('recipient_name'))))
    s = TaqdeerSettings.get()
    return render(request, 'taqdeer/archive.html', {
        'rows': rows,
        'window_days': s.visibility_days,
        'is_hr': user_is_hr(request.user),
    })
