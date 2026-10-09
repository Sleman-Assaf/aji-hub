# services/service_registry.py
"""Maps a Service.service_key (set by the admin on the Service model) to the
actual feature code that knows how to list a user's past requests and where
the 'new request' form lives.

Categories/services can be created in the admin before the underlying feature
is built — until a matching entry exists here, the service page just shows a
'coming soon' state instead of erroring.
"""
from django.urls import reverse


def _job_requisition_history(user):
    from .models import JobRequisition
    items = JobRequisition.objects.filter(requested_by=user).select_related('department').order_by('-created_at')
    rows = []
    for r in items:
        if r.status == JobRequisition.Status.APPROVED:
            state = 'done'
        elif r.status == JobRequisition.Status.REJECTED:
            state = 'rejected'
        else:
            state = 'pending'
        rows.append({
            'title': r.job_title,
            'subtitle': r.department.name if r.department else '',
            'status_label': r.stage_label(),
            'status_state': state,
            'created_at': r.created_at,
            'detail_url': reverse('services:job_requisition_detail', args=[r.pk]),
        })
    return rows


SERVICE_HANDLERS = {
    'job_requisition': {
        'get_history': _job_requisition_history,
        'new_url_name': 'services:job_requisition_new',
        'new_label': 'New Job Requisition',
    },
    'meeting_room_booking': {
        # Room booking is a live calendar, not a request/approval flow —
        # send the user straight to its own dedicated page.
        'redirect_url_name': 'services:room_booking',
    },
    'cafeteria': {
        # The cafeteria has its own menu + cart page.
        'redirect_url_name': 'cafeteria:home',
    },
    'car_booking': {
        # Like the rooms: a live availability screen, not a form that waits.
        'redirect_url_name': 'services:car_booking',
    },
    'meeting_packages': {
        # Facility Managers order meeting catering packages (paid by AJi).
        'redirect_url_name': 'cafeteria:package_order',
    },
}
