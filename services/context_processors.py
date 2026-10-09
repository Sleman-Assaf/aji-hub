# services/context_processors.py
from django.urls import reverse

from .models import JobRequisition
from core.context_processors import once_per_request


@once_per_request
def services_context(request):
    ctx = {'pending_approvals_count': 0, 'show_approvals_menu': False}

    if not request.user.is_authenticated:
        return ctx

    user_group_names = set(request.user.groups.values_list('name', flat=True))

    # Count items actionable by this user right now, across every approval stage.
    count = JobRequisition.objects.filter(
        status=JobRequisition.Status.PENDING_DIRECTOR, director=request.user,
    ).count()
    if 'HR Managers' in user_group_names:
        count += JobRequisition.objects.filter(status=JobRequisition.Status.PENDING_HR).count()
    if 'CEO' in user_group_names:
        count += JobRequisition.objects.filter(status=JobRequisition.Status.PENDING_CEO).count()

    ctx['pending_approvals_count'] = count
    ctx['show_approvals_menu'] = count > 0
    if count > 0:
        ctx['approvals_url'] = reverse('services:approvals_inbox')

    return ctx
