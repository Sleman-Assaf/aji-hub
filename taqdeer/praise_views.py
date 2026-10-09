"""Praise moderation, for when HR turns approval on.

Only reachable, and only meaningful, while praise_needs_approval is set. When it
is off the queue is empty by construction and the page says so rather than
pretending to be a feature nobody switched on.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import NotificationEvent as Event
from core.notify import notify

from .models import Praise, TaqdeerSettings
from .views import _hr_only


@login_required
def queue(request):
    """Everything waiting, and everything hidden."""
    _hr_only(request)
    s = TaqdeerSettings.get()
    waiting = (Praise.objects
               .filter(status=Praise.Status.PENDING, hidden_at__isnull=True)
               .select_related('giver', 'recipient', 'recipient__employee'))
    hidden = (Praise.objects.filter(hidden_at__isnull=False)
              .select_related('giver', 'recipient', 'hidden_by')[:30])
    return render(request, 'taqdeer/praise_queue.html', {
        'waiting': waiting, 'hidden': hidden,
        'moderation_on': s.praise_needs_approval,
        'is_hr': True,
    })


@login_required
@require_POST
def decide(request, pk):
    """Publish one, or decline it.

    The recipient is told on publication and not before — Kudos that may
    never appear should not announce itself first.
    """
    _hr_only(request)
    p = get_object_or_404(Praise.objects.select_related('giver', 'recipient'), pk=pk)
    action = request.POST.get('action')
    giver = p.giver.get_full_name() or p.giver.username

    if action == 'publish':
        p.status = Praise.Status.APPROVED
        p.hidden_at = None
        p.hidden_by = None
        p.save(update_fields=['status', 'hidden_at', 'hidden_by'])
        notify(p.recipient, Event.GENERAL,
               title=f'{giver} sent you Kudos',
               body=p.subject, link=reverse('taqdeer_home'), actor=request.user,
               case='kudos.published',
               values={'actor': giver, 'subject_line': p.subject,
                       'name': (p.recipient.get_full_name()
                                or p.recipient.username)})
        messages.success(request, 'Published.')

    elif action == 'decline':
        p.status = Praise.Status.REJECTED
        p.hidden_at = timezone.now()
        p.hidden_by = request.user
        p.save(update_fields=['status', 'hidden_at', 'hidden_by'])
        notify(p.giver, Event.GENERAL,
               title='Your Kudos was not published',
               body='HR reviewed it and did not publish it. Ask HR if you would '
                    'like to know why.',
               link=reverse('taqdeer_home'), actor=request.user,
               case='kudos.declined',
               values={'name': giver, 'subject_line': p.subject,
                       'recipient': (p.recipient.get_full_name()
                                     or p.recipient.username)})
        messages.success(request, 'Declined, and the sender was told.')

    elif action == 'restore':
        p.hidden_at = None
        p.hidden_by = None
        p.status = Praise.Status.APPROVED
        p.save(update_fields=['status', 'hidden_at', 'hidden_by'])
        messages.success(request, 'Back on the wall.')

    return redirect('taqdeer_praise_queue')
