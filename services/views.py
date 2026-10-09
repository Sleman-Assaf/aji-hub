# services/views.py
from datetime import date, datetime, timedelta, time as dt_time
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from core.models import Notification
from people.models import Department, Employee, Entity

from .models import (ApprovalLog, BookingInvite, JobRequisition, MeetingRoom, RoomBooking,
                     Service, ServiceCategory, RECEPTION_GROUP)
from .service_registry import SERVICE_HANDLERS

logger = logging.getLogger(__name__)


def _by_id(model, raw, **extra):
    """One row by primary key, or None — including when `raw` is not a number.

    `Model.objects.filter(pk='AJi Jordan')` does not come back empty: it raises
    ValueError, because the field is an integer. So a form that posts a label
    where an id belongs — a stale page, a hand-made request, a select rebuilt by
    a script that failed — produced a 500 instead of the validation message
    waiting two lines below it.

    Everything that reads an id straight out of request.POST goes through here.
    """
    raw = str(raw or '').strip()
    if not raw.isdigit():
        return None
    return model.objects.filter(pk=int(raw), **extra).first()


def _is_receptionist(user):
    """Runs the Bookings desk: meeting rooms, and now the cars too.

    Both names are accepted. The group was renamed by `setup_fleet`, and a
    database that has not had that run yet would otherwise lock the whole
    reception team out of their own approvals screen.
    """
    from .models import LEGACY_RECEPTION_GROUP
    return (user.is_superuser
            or user.groups.filter(
                name__in=(RECEPTION_GROUP, LEGACY_RECEPTION_GROUP)).exists())


def _whatsapp_user(user, message):
    """Best-effort WhatsApp message to a user's employee mobile number.
    Silently skips when the user has no mobile or WhatsApp is not configured."""
    try:
        from core.whatsapp import send_whatsapp
        employee = getattr(user, 'employee', None)
        mobile = employee.full_mobile() if employee else ''
        if mobile:
            send_whatsapp(mobile, message)
    except Exception as exc:
        logger.warning(f'[whatsapp] could not message {user}: {exc}')


def _director_for(user):
    """Resolve the requester's Director as their Employee.manager's linked User."""
    employee = getattr(user, 'employee', None)
    if employee and employee.manager and employee.manager.user:
        return employee.manager.user
    return None


def _can_act(user, req):
    if req.status == JobRequisition.Status.PENDING_DIRECTOR:
        return user == req.director
    if req.status == JobRequisition.Status.PENDING_HR:
        return user.groups.filter(name='HR Managers').exists()
    if req.status == JobRequisition.Status.PENDING_CEO:
        return user.groups.filter(name='CEO').exists()
    return False


def _heads_and_managers_for(department):
    """Employees who lead this department/unit: the department manager, any
    section heads within it, and anyone who manages other employees in it."""
    ids = set()
    if department.manager_id:
        ids.add(department.manager_id)
    for section in department.sections.all():
        if section.head_id:
            ids.add(section.head_id)
    manager_ids = (
        Employee.objects.filter(department=department)
        .exclude(manager_id__isnull=True)
        .values_list('manager_id', flat=True)
        .distinct()
    )
    ids |= set(manager_ids)
    return Employee.objects.filter(pk__in=ids, is_active=True).order_by('full_name')


@login_required
def department_list_api(request):
    entity_id = request.GET.get('entity')
    departments = Department.objects.filter(entity_id=entity_id).order_by('name') if entity_id else Department.objects.none()
    return JsonResponse({'departments': [{'id': d.pk, 'name': d.name} for d in departments]})


@login_required
def department_managers_api(request):
    department_id = request.GET.get('department')
    if not department_id:
        return JsonResponse({'managers': []})
    department = get_object_or_404(Department, pk=department_id)
    managers = _heads_and_managers_for(department)
    return JsonResponse({'managers': [{'id': m.pk, 'name': m.full_name, 'role': m.role} for m in managers]})


@login_required
def job_requisition_new(request):
    # Hiring Managers raise these. An Intranet Admin passes too, for the same
    # reason every other permission rule in the Hub lets one through: the person
    # who administers the module has to be able to open it, and one who cannot
    # see the form cannot check that it works.
    from core.models import is_intranet_admin
    if not (request.user.groups.filter(name='Hiring Managers').exists()
            or is_intranet_admin(request.user)):
        messages.error(request, "You don't have permission to access this service.")
        return redirect('home')

    if request.method == 'POST':
        director = _director_for(request.user)
        if not director:
            messages.error(
                request,
                "We couldn't find a director in your reporting line. "
                "Please contact HR to set your manager before submitting.",
            )
            return redirect('services:job_requisition_new')

        company = _by_id(Entity, request.POST.get('company'))
        department = _by_id(Department, request.POST.get('department'))
        reports_to = _by_id(Employee, request.POST.get('reports_to'))

        if not company or not department:
            messages.error(request, 'Please select a company and a department / unit.')
            return redirect('services:job_requisition_new')

        req = JobRequisition.objects.create(
            requested_by=request.user,
            job_title=request.POST.get('job_title', '').strip(),
            company=company,
            department=department,
            reports_to=reports_to,
            targeted_start_date=request.POST.get('targeted_start_date') or None,
            work_location=request.POST.get('work_location', '').strip(),
            business_need=request.POST.get('business_need', '').strip(),
            primary_responsibilities=request.POST.get('primary_responsibilities', '').strip(),
            key_qualifications=request.POST.get('key_qualifications', '').strip(),
            director=director,
        )
        Notification.objects.create(
            recipient=director,
            type=Notification.Type.INFO,
            title='New Job Requisition needs your approval',
            body=f"{request.user.get_full_name() or request.user.username} requested: {req.job_title}",
            link=f'/services/job-requisition/{req.pk}/',
        )
        messages.success(request, 'Job requisition submitted for approval.')
        return redirect('services:job_requisition_detail', pk=req.pk)

    return render(request, 'services/job_requisition_form.html', {
        'entities': Entity.objects.all().order_by('name'),
    })


@login_required
def job_requisition_detail(request, pk):
    req = get_object_or_404(JobRequisition, pk=pk)
    can_view = (
        request.user == req.requested_by
        or request.user == req.director
        or request.user.groups.filter(name__in=['HR Managers', 'CEO']).exists()
    )
    if not can_view:
        raise PermissionDenied

    logs_by_stage = {log.stage: log for log in req.logs.all()}
    director_log = logs_by_stage.get(JobRequisition.Status.PENDING_DIRECTOR)
    hr_log = logs_by_stage.get(JobRequisition.Status.PENDING_HR)
    ceo_log = logs_by_stage.get(JobRequisition.Status.PENDING_CEO)

    def stage_state(log, current_status_key):
        if log:
            return ('rejected', log.acted_at, log.comment) if log.decision == ApprovalLog.Decision.REJECTED \
                else ('done', log.acted_at, log.comment)
        if req.status == current_status_key:
            return ('current', None, '')
        return ('upcoming', None, '')

    director_state, director_time, director_comment = stage_state(director_log, JobRequisition.Status.PENDING_DIRECTOR)
    hr_state, hr_time, hr_comment = stage_state(hr_log, JobRequisition.Status.PENDING_HR)
    ceo_state, ceo_time, ceo_comment = stage_state(ceo_log, JobRequisition.Status.PENDING_CEO)

    stages = [
        {
            'label': 'Hiring Manager',
            'name': req.requested_by.get_full_name() or req.requested_by.username,
            'state': 'done',
            'time': req.created_at,
            'comment': '',
            'color': '#4FB4BE',
        },
        {
            'label': 'Director',
            'name': req.director.get_full_name() or req.director.username if req.director else '—',
            'state': director_state,
            'time': director_time,
            'comment': director_comment,
            'color': '#3B5EC7',
        },
        {
            'label': 'HR Manager',
            'name': req.hr_approver.get_full_name() or req.hr_approver.username if req.hr_approver else '—',
            'state': hr_state,
            'time': hr_time,
            'comment': hr_comment,
            'color': '#E15B4F',
        },
        {
            'label': 'CEO',
            'name': req.ceo_approver.get_full_name() or req.ceo_approver.username if req.ceo_approver else '—',
            'state': ceo_state,
            'time': ceo_time,
            'comment': ceo_comment,
            'color': '#EFB63D',
        },
    ]

    return render(request, 'services/job_requisition_detail.html', {
        'req': req,
        'can_act': _can_act(request.user, req),
        'stages': stages,
    })


@login_required
def approvals_inbox(request):
    qs = JobRequisition.objects.exclude(
        status__in=[JobRequisition.Status.APPROVED, JobRequisition.Status.REJECTED],
    ).select_related('requested_by', 'director', 'department', 'company')
    items = [r for r in qs if _can_act(request.user, r)]
    return render(request, 'services/approvals_inbox.html', {'items': items})


@login_required
def job_requisition_action(request, pk):
    req = get_object_or_404(JobRequisition, pk=pk)
    if request.method != 'POST' or not _can_act(request.user, req):
        raise PermissionDenied

    decision = request.POST.get('decision')
    comment = request.POST.get('comment', '').strip()
    stage = req.status

    ApprovalLog.objects.create(
        requisition=req, stage=stage, actor=request.user,
        decision=ApprovalLog.Decision.APPROVED if decision == 'approve' else ApprovalLog.Decision.REJECTED,
        comment=comment,
    )

    if decision == 'reject':
        req.status = JobRequisition.Status.REJECTED
        req.save()
        Notification.objects.create(
            recipient=req.requested_by,
            type=Notification.Type.WARNING,
            title='Job Requisition rejected',
            body=f'Your request for "{req.job_title}" was rejected.' + (f' Reason: {comment}' if comment else ''),
            link=f'/services/job-requisition/{req.pk}/',
        )
        messages.success(request, 'Requisition rejected.')
        return redirect('services:approvals_inbox')

    if stage == JobRequisition.Status.PENDING_DIRECTOR:
        req.status = JobRequisition.Status.PENDING_HR
        req.save()
        for hr_user in User.objects.filter(groups__name='HR Managers'):
            Notification.objects.create(
                recipient=hr_user, type=Notification.Type.INFO,
                title='Job Requisition needs HR approval',
                body=f'"{req.job_title}" was approved by the director and needs HR approval.',
                link=f'/services/job-requisition/{req.pk}/',
            )
    elif stage == JobRequisition.Status.PENDING_HR:
        req.hr_approver = request.user
        req.status = JobRequisition.Status.PENDING_CEO
        req.save()
        for ceo_user in User.objects.filter(groups__name='CEO'):
            Notification.objects.create(
                recipient=ceo_user, type=Notification.Type.INFO,
                title='Job Requisition needs CEO approval',
                body=f'"{req.job_title}" was approved by HR and needs CEO approval.',
                link=f'/services/job-requisition/{req.pk}/',
            )
    elif stage == JobRequisition.Status.PENDING_CEO:
        req.ceo_approver = request.user
        req.status = JobRequisition.Status.APPROVED
        req.save()
        Notification.objects.create(
            recipient=req.requested_by, type=Notification.Type.SUCCESS,
            title='Job Requisition approved',
            body=f'Your request for "{req.job_title}" has been fully approved.',
            link=f'/services/job-requisition/{req.pk}/',
        )

    messages.success(request, 'Approved and forwarded to the next stage.')
    return redirect('services:approvals_inbox')


# ── My AJi: category / service browser ─────────────────────────────────────

def _user_can_see_category(user, category, pending_approvals_count):
    if category.category_type == ServiceCategory.CategoryType.APPROVALS:
        return pending_approvals_count > 0
    return any(svc.user_has_access(user) for svc in category.services.filter(is_active=True))


@login_required
def my_aji(request):
    user_group_names = set(request.user.groups.values_list('name', flat=True))
    pending_count = JobRequisition.objects.filter(
        status=JobRequisition.Status.PENDING_DIRECTOR, director=request.user,
    ).count()
    if 'HR Managers' in user_group_names:
        pending_count += JobRequisition.objects.filter(status=JobRequisition.Status.PENDING_HR).count()
    if 'CEO' in user_group_names:
        pending_count += JobRequisition.objects.filter(status=JobRequisition.Status.PENDING_CEO).count()

    categories = []
    for cat in ServiceCategory.objects.filter(is_active=True).prefetch_related('services'):
        if _user_can_see_category(request.user, cat, pending_count):
            categories.append({
                'obj': cat,
                'pending_count': pending_count if cat.category_type == ServiceCategory.CategoryType.APPROVALS else 0,
            })

    return render(request, 'services/my_aji.html', {
        'categories': categories,
        'pending_approvals_count': pending_count,
    })


@login_required
def service_category_detail(request, pk):
    category = get_object_or_404(ServiceCategory, pk=pk, is_active=True)

    if category.category_type == ServiceCategory.CategoryType.APPROVALS:
        return redirect('services:approvals_inbox')

    services = [svc for svc in category.services.filter(is_active=True) if svc.user_has_access(request.user)]
    if not services:
        raise PermissionDenied

    return render(request, 'services/category_detail.html', {
        'category': category,
        'services': services,
    })


@login_required
def service_detail(request, pk):
    service = get_object_or_404(Service, pk=pk, is_active=True)
    if not service.user_has_access(request.user):
        raise PermissionDenied

    handler = SERVICE_HANDLERS.get(service.service_key)

    if handler and handler.get('redirect_url_name'):
        return redirect(handler['redirect_url_name'])

    history = handler['get_history'](request.user) if handler else []
    new_url = reverse(handler['new_url_name']) if handler else None
    new_label = handler.get('new_label', f'New {service.name}') if handler else None

    return render(request, 'services/service_detail.html', {
        'service': service,
        'history': history,
        'new_url': new_url,
        'new_label': new_label,
        'coming_soon': handler is None,
    })


# ── Meeting Room Booking ────────────────────────────────────────────────────

WORK_START = dt_time(8, 0)
WORK_END = dt_time(17, 0)
SLOT_MINUTES = 30


def _time_slots():
    """['08:00','08:30', ... ,'16:30'] — the start time of each bookable half-hour cell."""
    slots = []
    cur = datetime.combine(datetime.today(), WORK_START)
    end = datetime.combine(datetime.today(), WORK_END)
    while cur < end:
        slots.append(cur.strftime('%H:%M'))
        cur = cur + timedelta(minutes=SLOT_MINUTES)
    return slots


@login_required
def room_booking(request):
    from cafeteria.models import MeetingPackage
    rooms = MeetingRoom.objects.filter(is_active=True)

    # Meetings this person has been asked to, from today onwards. Past ones are
    # dropped: an invitation to a meeting that has already happened is history,
    # not something to answer.
    my_invites = (BookingInvite.objects
                  .filter(user=request.user, booking__date__gte=date.today())
                  .exclude(booking__status=RoomBooking.Status.REJECTED)
                  .select_related('booking__room', 'booking__booked_by')
                  .order_by('booking__date', 'booking__start_time'))

    # Their own meetings that have guests, so they can see who has replied.
    my_meetings = (RoomBooking.objects
                   .filter(booked_by=request.user, date__gte=date.today())
                   .exclude(status=RoomBooking.Status.REJECTED)
                   .prefetch_related('invites__user')
                   .select_related('room')
                   .order_by('date', 'start_time'))
    my_meetings = [b for b in my_meetings if b.invites.all()]

    return render(request, 'services/room_booking.html', {
        'rooms': rooms,
        'my_invites': my_invites,
        'awaiting_count': sum(1 for i in my_invites
                              if i.response == BookingInvite.Response.PENDING),
        'my_meetings': my_meetings,
        'people_search_url': reverse('exchange_people_search'),
        'time_slots': _time_slots(),
        'work_start': WORK_START.strftime('%H:%M'),
        'work_end': WORK_END.strftime('%H:%M'),
        'packages': MeetingPackage.objects.filter(is_active=True),
        'is_receptionist': _is_receptionist(request.user),
        'pending_requests_count': (RoomBooking.objects.filter(status=RoomBooking.Status.PENDING).count()
                                   if _is_receptionist(request.user) else 0),
    })


@login_required
def room_requests(request):
    """Reception Desk inbox: approve / reject / modify pending booking requests."""
    if not _is_receptionist(request.user):
        raise PermissionDenied
    pending = (RoomBooking.objects.filter(status=RoomBooking.Status.PENDING)
               .select_related('room', 'booked_by').order_by('date', 'start_time'))
    recent = (RoomBooking.objects.exclude(status=RoomBooking.Status.PENDING)
              .select_related('room', 'booked_by', 'reviewed_by').order_by('-created_at')[:20])
    return render(request, 'services/room_requests.html', {
        'pending': pending,
        'recent': recent,
        'rooms': MeetingRoom.objects.filter(is_active=True),
        'time_slots': _time_slots(),
    })


@login_required
def room_request_review(request, pk):
    """Reception approves (optionally after modifying room / date / time) or
    rejects a booking request. On approval, a waiting catering request is
    forwarded to the Facility Manager."""
    if request.method != 'POST' or not _is_receptionist(request.user):
        raise PermissionDenied

    from cafeteria.models import CateringRequest, FACILITY_GROUP
    from django.contrib.auth.models import Group

    booking = get_object_or_404(
        RoomBooking.objects.select_related('room', 'booked_by'),
        pk=pk, status=RoomBooking.Status.PENDING,
    )
    action = request.POST.get('action')
    comment = (request.POST.get('comment') or '')[:255]
    catering = CateringRequest.objects.filter(booking=booking).first()

    if action == 'reject':
        booking.status = RoomBooking.Status.REJECTED
        booking.reviewed_by = request.user
        booking.review_comment = comment
        booking.save(update_fields=['status', 'reviewed_by', 'review_comment'])
        if catering and catering.status == CateringRequest.Status.WAITING_ROOM:
            catering.delete()
        Notification.objects.create(
            recipient=booking.booked_by, type=Notification.Type.WARNING,
            title='Room booking rejected',
            body=(f'Your booking of {booking.where} on {booking.date} '
                  f'{booking.start_time.strftime("%H:%M")}–{booking.end_time.strftime("%H:%M")} was rejected '
                  f'by the reception desk.{" — " + comment if comment else ""}'),
            link=reverse('services:room_booking'),
        )
        messages.success(request, 'Booking request rejected. The requester has been notified.')
        return redirect('services:room_requests')

    if action == 'approve':
        # The receptionist can modify the room, date and time before approving.
        new_room = (_by_id(MeetingRoom, request.POST.get('room_id'), is_active=True)
                    or booking.room)
        try:
            new_date = datetime.strptime(request.POST.get('date', ''), '%Y-%m-%d').date()
            new_start = datetime.strptime(request.POST.get('start_time', ''), '%H:%M').time()
            new_end = datetime.strptime(request.POST.get('end_time', ''), '%H:%M').time()
        except ValueError:
            messages.error(request, 'Invalid date/time.')
            return redirect('services:room_requests')

        if new_start >= new_end:
            messages.error(request, 'End time must be after start time.')
            return redirect('services:room_requests')
        if new_start < WORK_START or new_end > WORK_END:
            messages.error(request, 'Bookings must fall within working hours (08:00–17:00).')
            return redirect('services:room_requests')

        conflicts = (RoomBooking.objects.filter(room=new_room, date=new_date)
                     .exclude(pk=booking.pk).exclude(status=RoomBooking.Status.REJECTED))
        for b in conflicts:
            if b.overlaps(new_start, new_end):
                messages.error(
                    request,
                    f'That slot overlaps an existing booking in {new_room.name} '
                    f'({b.start_time.strftime("%H:%M")}–{b.end_time.strftime("%H:%M")} by '
                    f'{b.booked_by.get_full_name() or b.booked_by.username}).',
                )
                return redirect('services:room_requests')

        modified = (new_room != booking.room or new_date != booking.date
                    or new_start != booking.start_time or new_end != booking.end_time)
        booking.room = new_room
        booking.date = new_date
        booking.start_time = new_start
        booking.end_time = new_end
        booking.status = RoomBooking.Status.APPROVED
        booking.reviewed_by = request.user
        booking.review_comment = comment
        booking.save()

        Notification.objects.create(
            recipient=booking.booked_by, type=Notification.Type.SUCCESS,
            title='Room booking approved' + (' (modified)' if modified else ''),
            body=(f'{booking.where} on {booking.date} '
                  f'{booking.start_time.strftime("%H:%M")}–{booking.end_time.strftime("%H:%M")} is confirmed.'
                  f'{" — " + comment if comment else ""}'),
            link=reverse('services:room_booking'),
        )

        # Forward the waiting catering request to the Facility Manager
        if catering and catering.status == CateringRequest.Status.WAITING_ROOM:
            catering.status = CateringRequest.Status.PENDING
            catering.save(update_fields=['status', 'updated_at'])
            fm_group = Group.objects.filter(name=FACILITY_GROUP).first()
            if fm_group:
                fm_body = (f'{booking.booked_by.get_full_name() or booking.booked_by.username} requested '
                           f'"{catering.package.name}" ({catering.package.get_level_display()}) for '
                           f'{catering.attendees_count} attendees — {booking.where}, {booking.date} '
                           f'{booking.start_time.strftime("%H:%M")}–{booking.end_time.strftime("%H:%M")}.')
                for fm_user in fm_group.user_set.all():
                    Notification.objects.create(
                        recipient=fm_user, type=Notification.Type.INFO,
                        title='Catering package needs your approval',
                        body=fm_body,
                        link=reverse('cafeteria:package_order'),
                    )
                    _whatsapp_user(
                        fm_user,
                        f'🍽 AJi Hub — New catering package request\n{fm_body}\n'
                        f'Review it on the hub: Meeting Packages page.',
                    )

        messages.success(
            request,
            f'Booking approved{" with modifications" if modified else ""}.'
            f'{" Catering request forwarded to the Facility Manager." if catering else ""}',
        )
        return redirect('services:room_requests')

    messages.error(request, 'Unknown action.')
    return redirect('services:room_requests')


@login_required
def room_schedule_api(request):
    date_str = request.GET.get('date')
    try:
        the_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Invalid or missing date (expected YYYY-MM-DD).'}, status=400)

    rooms_data = []
    for room in MeetingRoom.objects.filter(is_active=True):
        bookings = RoomBooking.objects.filter(room=room, date=the_date).exclude(status=RoomBooking.Status.REJECTED).select_related('booked_by')
        rooms_data.append({
            'id': room.pk,
            'name': room.name,
            'chairs': room.chairs,
            'bookings': [
                {
                    'id': b.pk,
                    'start': b.start_time.strftime('%H:%M'),
                    'end': b.end_time.strftime('%H:%M'),
                    'title': b.title,
                    'meeting_type': b.meeting_type,
                    'attendees_count': b.attendees_count,
                    'catering_requirements': b.catering_requirements,
                    'it_requirements': b.it_requirements,
                    'booked_by': b.booked_by.get_full_name() or b.booked_by.username,
                    'is_own': b.booked_by_id == request.user.pk,
                }
                for b in bookings
            ],
        })

    return JsonResponse({'date': date_str, 'rooms': rooms_data})


def _parse_booking_extra_fields(request):
    meeting_type = request.POST.get('meeting_type', 'internal').strip()
    if meeting_type not in RoomBooking.MeetingType.values:
        meeting_type = RoomBooking.MeetingType.INTERNAL
    attendees_raw = request.POST.get('attendees_count', '').strip()
    try:
        attendees_count = int(attendees_raw) if attendees_raw else None
        if attendees_count is not None and attendees_count < 1:
            attendees_count = None
    except ValueError:
        attendees_count = None
    return {
        'title': request.POST.get('title', '').strip(),
        'meeting_type': meeting_type,
        'attendees_count': attendees_count,
        'catering_requirements': request.POST.get('catering_requirements', '').strip(),
        'it_requirements': request.POST.get('it_requirements', '').strip(),
    }


def _booking_json(booking, request):
    try:
        catering = getattr(booking, 'catering_request', None)
        catering_package = catering.package_id if catering else None
        catering_status = catering.get_status_display() if catering else None
    except Exception:
        # e.g. cafeteria migration not applied yet — never break the schedule
        catering_package = None
        catering_status = None
    return {
        'id': booking.pk,
        'start': booking.start_time.strftime('%H:%M'),
        'end': booking.end_time.strftime('%H:%M'),
        'title': booking.title,
        'meeting_type': booking.meeting_type,
        'attendees_count': booking.attendees_count,
        'catering_requirements': booking.catering_requirements,
        'catering_package': catering_package,
        'catering_status': catering_status,
        'it_requirements': booking.it_requirements,
        'status': booking.status,
        'status_label': booking.get_status_display(),
        'booked_by': booking.booked_by.get_full_name() or booking.booked_by.username,
        'is_own': booking.booked_by_id == request.user.pk,
        # So reopening a booking to edit it shows the people already invited.
        # Without this an edit would post two empty boxes and uninvite them all.
        'invites': [
            {'id': i.user_id, 'kind': i.kind, 'response': i.response,
             'name': i.user.get_full_name() or i.user.username}
            for i in booking.invites.all()
        ],
        'response_summary': booking.response_summary,
    }


@login_required
def room_week_schedule_api(request):
    start_str = request.GET.get('start')
    try:
        week_start = datetime.strptime(start_str, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return JsonResponse({'error': 'Invalid or missing start date (expected YYYY-MM-DD).'}, status=400)

    days = [week_start + timedelta(days=i) for i in range(7)]
    day_strs = [d.strftime('%Y-%m-%d') for d in days]

    rooms_data = []
    for room in MeetingRoom.objects.filter(is_active=True):
        bookings = RoomBooking.objects.filter(room=room, date__in=days).exclude(status=RoomBooking.Status.REJECTED).select_related('booked_by')
        by_date = {d: [] for d in day_strs}
        for b in bookings:
            by_date[b.date.strftime('%Y-%m-%d')].append(_booking_json(b, request))
        rooms_data.append({'id': room.pk, 'name': room.name, 'chairs': room.chairs, 'bookings_by_date': by_date})

    return JsonResponse({'days': day_strs, 'rooms': rooms_data})


def _meeting_when(booking):
    """"Tue 12 Aug, 14:00–15:00 in Boardroom" — one line, used in every notice.

    The day number is read off the date rather than formatted with %-d, which is
    a GNU extension and produces no day at all on Windows, where this runs.
    """
    d = booking.date
    return (f'{d.strftime("%a")} {d.day} {d.strftime("%b")}, '
            f'{booking.start_time.strftime("%H:%M")}–{booking.end_time.strftime("%H:%M")} '
            f'in {booking.where}')


def _sync_invites(request, booking, notify_new=True):
    """Put the posted invitee lists onto the booking, and tell the newcomers.

    Returns (added, removed). Only genuinely new people are notified — editing a
    meeting to move it by ten minutes should not re-invite everybody who was
    already coming, and re-posting the same list must be a no-op.

    Somebody moved from optional to required keeps their reply: they had already
    said yes to the meeting, and being told it now matters more does not make
    that answer untrue.
    """
    wanted = {}
    for kind, field in ((BookingInvite.Kind.REQUIRED, 'required'),
                        (BookingInvite.Kind.OPTIONAL, 'optional')):
        for raw in request.POST.getlist(field):
            try:
                pk = int(raw)
            except (TypeError, ValueError):
                continue
            # Required wins if somebody is listed in both boxes.
            if pk not in wanted or kind == BookingInvite.Kind.REQUIRED:
                wanted[pk] = kind
    # The organiser is not a guest at their own meeting.
    wanted.pop(booking.booked_by_id, None)

    valid = set(User.objects.filter(pk__in=wanted, is_active=True).values_list('pk', flat=True))
    wanted = {pk: k for pk, k in wanted.items() if pk in valid}

    existing = {i.user_id: i for i in booking.invites.all()}
    added, removed = [], []

    for pk, kind in wanted.items():
        inv = existing.get(pk)
        if inv is None:
            added.append(BookingInvite.objects.create(booking=booking, user_id=pk, kind=kind))
        elif inv.kind != kind:
            inv.kind = kind
            inv.save(update_fields=['kind'])

    for pk, inv in existing.items():
        if pk not in wanted:
            removed.append(inv.user)
            inv.delete()

    if notify_new and added:
        from core import notify as notify_mod
        organiser = booking.booked_by.get_full_name() or booking.booked_by.username
        link = reverse('services:room_booking')
        for inv in added:
            notify_mod.notify(
                inv.user, notify_mod.Event.GENERAL,
                f'Meeting invitation: {booking.title or booking.where}',
                body=(f'{organiser} invited you ({inv.get_kind_display().lower()}) to '
                      f'{_meeting_when(booking)}. Open Room Booking to reply.'),
                link=link, actor=request.user,
                case='rooms.invited',
                values={'name': inv.user.get_full_name() or inv.user.username,
                        'actor': organiser,
                        'attendance': inv.get_kind_display().lower(),
                        'meeting': booking.title or booking.where,
                        'when': _meeting_when(booking)})

    if removed:
        from core import notify as notify_mod
        for user in removed:
            notify_mod.notify(
                user, notify_mod.Event.GENERAL,
                f'Meeting cancelled for you: {booking.title or booking.where}',
                body=f'You are no longer invited to {_meeting_when(booking)}.',
                link=reverse('services:room_booking'), actor=request.user,
                case='rooms.uninvited',
                values={'name': user.get_full_name() or user.username,
                        'meeting': booking.title or booking.where,
                        'when': _meeting_when(booking),
                        'actor': (request.user.get_full_name()
                                  or request.user.username)})

    return added, removed


@login_required
def room_booking_create(request):
    if request.method != 'POST':
        raise PermissionDenied

    # A meeting does not have to be in a room: a call across offices has a time,
    # attendees and a Teams link and nothing to reserve. One of the two has to
    # be true, though — a meeting that is nowhere is not a meeting.
    wants_online = request.POST.get('is_online') in ('1', 'true', 'on', 'yes')
    room_id = request.POST.get('room_id') or ''
    if room_id:
        room = get_object_or_404(MeetingRoom, pk=room_id, is_active=True)
    elif wants_online:
        room = None
    else:
        return JsonResponse({'ok': False,
                             'error': 'Choose a room, or turn on Teams.'}, status=400)

    date_str = request.POST.get('date', '')
    start_str = request.POST.get('start_time', '')
    end_str = request.POST.get('end_time', '')
    fields = _parse_booking_extra_fields(request)
    fields['is_online'] = wants_online
    fields['description'] = (request.POST.get('description') or '').strip()
    fields['online_url'] = (request.POST.get('online_url') or '').strip()

    try:
        the_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        start_time = datetime.strptime(start_str, '%H:%M').time()
        end_time = datetime.strptime(end_str, '%H:%M').time()
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'Invalid date/time.'}, status=400)

    if start_time >= end_time:
        return JsonResponse({'ok': False, 'error': 'End time must be after start time.'}, status=400)
    if start_time < WORK_START or end_time > WORK_END:
        return JsonResponse({'ok': False, 'error': 'Bookings must fall within working hours (08:00–17:00).'}, status=400)

    # Only a real room can be double-booked. Two Teams meetings at once are the
    # organiser's problem, not the building's.
    conflicts = (RoomBooking.objects.filter(room=room, date=the_date)
                 .exclude(status=RoomBooking.Status.REJECTED)) if room else []
    for b in conflicts:
        if b.overlaps(start_time, end_time):
            return JsonResponse({
                'ok': False,
                'error': f'That slot overlaps an existing booking ({b.start_time.strftime("%H:%M")}–{b.end_time.strftime("%H:%M")} by {b.booked_by.get_full_name() or b.booked_by.username}).',
            }, status=409)

    # ── Mandatory catering & IT choices ──
    from cafeteria.models import CateringRequest, MeetingPackage, FACILITY_GROUP
    from django.contrib.auth.models import Group

    catering_needed = request.POST.get('catering_needed', '').strip()
    it_needed = request.POST.get('it_needed', '').strip()
    if catering_needed not in ('yes', 'no'):
        return JsonResponse({'ok': False, 'error': 'Please select whether you need catering (yes / no).'}, status=400)
    if it_needed not in ('yes', 'no'):
        return JsonResponse({'ok': False, 'error': 'Please select whether you need IT requirements (yes / no).'}, status=400)

    package = None
    if catering_needed == 'yes':
        package = _by_id(MeetingPackage, request.POST.get('catering_package'),
                         is_active=True)
        if not package:
            return JsonResponse({'ok': False, 'error': 'Please choose a catering package.'}, status=400)
        if not fields.get('attendees_count'):
            return JsonResponse({'ok': False, 'error': 'Please enter how many attendees (needed for the catering package).'}, status=400)
    else:
        fields['catering_requirements'] = ''

    if it_needed == 'yes':
        if not fields.get('it_requirements'):
            return JsonResponse({'ok': False, 'error': 'Please specify the IT requirements.'}, status=400)
    else:
        fields['it_requirements'] = ''

    booking = RoomBooking.objects.create(
        room=room, booked_by=request.user, date=the_date,
        start_time=start_time, end_time=end_time, **fields,
    )
    # A free room taken for an internal meeting is not a decision anybody has to
    # make, and holding it "pending" made the calendar lie about what was
    # booked. Reception still sees it; they simply do not have to act. Anything
    # external, or with catering attached, still waits for them.
    if not (package or booking.needs_reception):
        booking.status = RoomBooking.Status.APPROVED
        booking.save(update_fields=['status'])

    # Whoever was named in the two invitee boxes, each told once.
    _sync_invites(request, booking)

    # The booking is a request — it goes to the Reception Desk for approval.
    # If catering was requested it waits until the room is approved, then goes
    # to the Facility Manager.
    catering_requested = False
    if package:
        CateringRequest.objects.create(
            booking=booking,
            package=package,
            attendees_count=fields['attendees_count'],
            notes=fields.get('catering_requirements', ''),
            requested_by=request.user,
            status=CateringRequest.Status.WAITING_ROOM,
        )
        catering_requested = True

    # Only tell Reception about a room. A Teams-only meeting is nothing to do
    # with the front desk, and the title says whether they must act or are just
    # being kept informed — "needs your approval" on something already confirmed
    # is how an inbox stops being read.
    reception_group = Group.objects.filter(name=RECEPTION_GROUP).first() if room else None
    if reception_group:
        waiting = booking.status == RoomBooking.Status.PENDING
        for rec_user in reception_group.user_set.all():
            Notification.objects.create(
                recipient=rec_user, type=Notification.Type.INFO,
                title=('Room booking needs your approval' if waiting
                       else 'Room booked'),
                body=(f'{request.user.get_full_name() or request.user.username} requested {room.name} on '
                      f'{the_date} {start_time.strftime("%H:%M")}–{end_time.strftime("%H:%M")}'
                      f'{" with catering (" + package.name + ")" if package else ""}.'),
                link=reverse('services:room_requests'),
            )

    payload = _booking_json(booking, request)
    return JsonResponse({'ok': True, 'booking': payload, 'catering_requested': catering_requested})


@login_required
def room_booking_update(request, pk):
    if request.method != 'POST':
        raise PermissionDenied
    booking = get_object_or_404(RoomBooking, pk=pk)
    if booking.booked_by_id != request.user.pk and not request.user.is_superuser:
        raise PermissionDenied

    from cafeteria.models import CateringRequest, MeetingPackage, FACILITY_GROUP
    from django.contrib.auth.models import Group

    fields = _parse_booking_extra_fields(request)
    catering_needed = request.POST.get('catering_needed', '').strip()
    it_needed = request.POST.get('it_needed', '').strip()
    if catering_needed not in ('yes', 'no'):
        return JsonResponse({'ok': False, 'error': 'Please select whether you need catering (yes / no).'}, status=400)
    if it_needed not in ('yes', 'no'):
        return JsonResponse({'ok': False, 'error': 'Please select whether you need IT requirements (yes / no).'}, status=400)

    package = None
    if catering_needed == 'yes':
        package = _by_id(MeetingPackage, request.POST.get('catering_package'),
                         is_active=True)
        if not package:
            return JsonResponse({'ok': False, 'error': 'Please choose a catering package.'}, status=400)
        if not fields.get('attendees_count'):
            return JsonResponse({'ok': False, 'error': 'Please enter how many attendees (needed for the catering package).'}, status=400)
    else:
        fields['catering_requirements'] = ''

    if it_needed == 'yes':
        if not fields.get('it_requirements'):
            return JsonResponse({'ok': False, 'error': 'Please specify the IT requirements.'}, status=400)
    else:
        fields['it_requirements'] = ''

    for key, value in fields.items():
        setattr(booking, key, value)
    booking.save(update_fields=list(fields.keys()))

    # Only if the form carried the invitee boxes. An edit posted from a screen
    # that has no invitee fields must not read that as "remove everybody".
    if 'invitees_posted' in request.POST:
        _sync_invites(request, booking)

    # Sync the catering request with the edited booking
    existing = CateringRequest.objects.filter(booking=booking).first()
    editable_states = (CateringRequest.Status.PENDING, CateringRequest.Status.WAITING_ROOM)
    if catering_needed == 'yes':
        if existing and existing.status in editable_states:
            # not yet reviewed — just update the requested package/attendees/notes
            existing.package = package
            existing.attendees_count = fields['attendees_count']
            existing.notes = fields.get('catering_requirements', '')
            existing.save(update_fields=['package', 'attendees_count', 'notes', 'updated_at'])
        elif not existing:
            room_approved = booking.status == RoomBooking.Status.APPROVED
            CateringRequest.objects.create(
                booking=booking, package=package,
                attendees_count=fields['attendees_count'],
                notes=fields.get('catering_requirements', ''),
                requested_by=request.user,
                status=(CateringRequest.Status.PENDING if room_approved
                        else CateringRequest.Status.WAITING_ROOM),
            )
            if room_approved:
                fm_group = Group.objects.filter(name=FACILITY_GROUP).first()
                if fm_group:
                    fm_body = (f'{request.user.get_full_name() or request.user.username} requested '
                               f'"{package.name}" ({package.get_level_display()}) for {fields["attendees_count"]} '
                               f'attendees — {booking.where}, {booking.date} '
                               f'{booking.start_time.strftime("%H:%M")}–{booking.end_time.strftime("%H:%M")}.')
                    for fm_user in fm_group.user_set.all():
                        Notification.objects.create(
                            recipient=fm_user, type=Notification.Type.INFO,
                            title='Catering package needs your approval',
                            body=fm_body,
                            link=reverse('cafeteria:package_order'),
                        )
                        _whatsapp_user(
                            fm_user,
                            f'🍽 AJi Hub — New catering package request\n{fm_body}\n'
                            f'Review it on the hub: Meeting Packages page.',
                        )
        # already approved/rejected requests are left untouched
    elif existing and existing.status in editable_states:
        # catering switched to 'no' before review — withdraw the request
        existing.delete()

    return JsonResponse({'ok': True, 'booking': _booking_json(booking, request)})


@login_required
def room_booking_cancel(request, pk):
    if request.method != 'POST':
        raise PermissionDenied
    booking = get_object_or_404(RoomBooking, pk=pk)
    if booking.booked_by_id != request.user.pk and not request.user.is_superuser:
        raise PermissionDenied
    # Tell the guests before the meeting stops existing, while there is still
    # something to describe.
    guests = [i.user for i in booking.invites.select_related('user')]
    if guests:
        from core import notify as notify_mod
        when = _meeting_when(booking)
        subject = booking.title or booking.where
        for user in guests:
            notify_mod.notify(
                user, notify_mod.Event.GENERAL, f'Meeting cancelled: {subject}',
                body=f'{when} is no longer happening.',
                link=reverse('services:room_booking'), actor=request.user,
                case='rooms.cancelled',
                values={'name': user.get_full_name() or user.username,
                        'meeting': subject, 'when': when,
                        'actor': (request.user.get_full_name()
                                  or request.user.username)})
    booking.delete()
    return JsonResponse({'ok': True})


@login_required
def meeting_invite_respond(request, pk):
    """Accept or decline an invitation. Only the person invited may answer."""
    if request.method != 'POST':
        raise PermissionDenied
    invite = get_object_or_404(BookingInvite.objects.select_related('booking__room', 'booking__booked_by'),
                               pk=pk, user=request.user)

    answer = (request.POST.get('response') or '').strip()
    if answer not in (BookingInvite.Response.ACCEPTED, BookingInvite.Response.DECLINED):
        return JsonResponse({'ok': False, 'error': 'Choose accept or decline.'}, status=400)

    if invite.response != answer:
        invite.response = answer
        invite.responded_at = timezone.now()
        invite.save(update_fields=['response', 'responded_at'])

        # The organiser is the one waiting on this, so they hear about it.
        from core import notify as notify_mod
        who = request.user.get_full_name() or request.user.username
        booking = invite.booking
        notify_mod.notify(
            booking.booked_by, notify_mod.Event.GENERAL,
            f'{who} {invite.get_response_display().lower()}: '
            f'{booking.title or booking.where}',
            body=f'{_meeting_when(booking)}. {booking.response_summary}.',
            link=reverse('services:room_booking'), actor=request.user,
            case='rooms.responded',
            values={'actor': who,
                    'response': invite.get_response_display().lower(),
                    'meeting': booking.title or booking.where,
                    'when': _meeting_when(booking),
                    'summary': booking.response_summary,
                    'name': (booking.booked_by.get_full_name()
                             or booking.booked_by.username)})

    return JsonResponse({'ok': True, 'response': invite.response,
                         'summary': invite.booking.response_summary})
