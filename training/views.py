# training/views.py
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date

from core.models import user_is_hr
from .models import TrainingAttachment, TrainingRequest


def _decimal(raw):
    try:
        return round(float(str(raw).replace(',', '')), 2)
    except (TypeError, ValueError):
        return None


def _save_fields(request, obj):
    obj.course_name = request.POST.get('course_name', '').strip()
    obj.provider = request.POST.get('provider', '').strip()
    obj.business_need = request.POST.get('business_need', '').strip()
    obj.justification = request.POST.get('justification', '').strip()
    obj.expected_benefits = request.POST.get('expected_benefits', '').strip()
    obj.comments = request.POST.get('comments', '').strip()
    obj.estimated_cost = _decimal(request.POST.get('estimated_cost'))
    obj.currency = request.POST.get('currency', '').strip() or 'JOD'
    obj.preferred_from = parse_date(request.POST.get('preferred_from') or '') or None
    obj.preferred_to = parse_date(request.POST.get('preferred_to') or '') or None
    return obj


def _validate(request):
    errors = {}
    if not request.POST.get('course_name', '').strip():
        errors['course_name'] = 'Which course or programme?'
    if not request.POST.get('business_need', '').strip():
        errors['business_need'] = 'Describe the business need.'
    if not request.POST.get('justification', '').strip():
        errors['justification'] = 'Add a business justification.'
    d_from = parse_date(request.POST.get('preferred_from') or '')
    d_to = parse_date(request.POST.get('preferred_to') or '')
    if d_from and d_to and d_to < d_from:
        errors['preferred_to'] = 'The end date is before the start date.'
    return errors


@login_required
def training_submit(request):
    """Handles the Expression of Interest modal on People & Culture."""
    if request.method != 'POST':
        return redirect('training_list')

    errors = _validate(request)
    if errors:
        for msg in errors.values():
            messages.error(request, msg)
        return redirect('people_culture')

    req = _save_fields(request, TrainingRequest(employee=request.user))
    req.save()
    for upload in request.FILES.getlist('attachments'):
        TrainingAttachment.objects.create(
            request=req, file=upload, uploaded_by=request.user)
    req.submit(actor=request.user)

    messages.success(request, 'Expression of interest submitted.')
    return redirect('training_detail', pk=req.pk)


@login_required
def training_list(request):
    """Mine, plus anything awaiting me as a manager or as HR."""
    mine = TrainingRequest.objects.filter(employee=request.user)
    to_approve = TrainingRequest.objects.filter(
        manager=request.user, stage=TrainingRequest.Stage.PENDING_MANAGER)

    hr_queue = []
    if user_is_hr(request.user):
        hr_queue = TrainingRequest.objects.filter(stage__in=[
            TrainingRequest.Stage.PENDING_HR,
            TrainingRequest.Stage.APPROVED,
            TrainingRequest.Stage.SCHEDULED,
        ]).select_related('employee')

    return render(request, 'training/list.html', {
        'mine': mine,
        'to_approve': to_approve,
        'hr_queue': hr_queue,
        'is_hr': user_is_hr(request.user),
    })


@login_required
def training_detail(request, pk):
    req = get_object_or_404(
        TrainingRequest.objects.select_related('employee', 'manager', 'hr_owner'), pk=pk)
    if not req.can_view(request.user):
        raise Http404

    return render(request, 'training/detail.html', {
        'req': req,
        'events': req.events.select_related('actor').all(),
        'attachments': req.attachments.all(),
        'is_manager': req.can_act_as_manager(request.user),
        'is_hr': req.can_act_as_hr(request.user),
        'is_owner': request.user == req.employee,
    })


@login_required
def training_action(request, pk, action):
    req = get_object_or_404(TrainingRequest, pk=pk)
    if not req.can_view(request.user):
        raise Http404
    if request.method != 'POST':
        return redirect('training_detail', pk=pk)

    note = request.POST.get('note', '').strip()
    manager_actions = {'approve': req.manager_approve,
                       'reject': req.manager_reject,
                       'return': req.manager_return}
    hr_actions = {'hr_approve': None, 'hr_reject': req.hr_reject,
                  'hr_info': req.hr_request_info,
                  'scheduled': None, 'completed': None}

    if action in manager_actions:
        if not req.can_act_as_manager(request.user):
            messages.error(request, "This request isn't waiting on your approval.")
            return redirect('training_detail', pk=pk)
        manager_actions[action](request.user, note)
        messages.success(request, 'Decision recorded.')

    elif action in hr_actions:
        if not req.can_act_as_hr(request.user):
            messages.error(request, "You can't action this request right now.")
            return redirect('training_detail', pk=pk)
        if action == 'hr_approve':
            req.hr_approve(request.user, note, _decimal(request.POST.get('budget')))
        elif action == 'scheduled':
            req.mark_scheduled(request.user,
                               parse_date(request.POST.get('on') or '') or None, note)
        elif action == 'completed':
            req.mark_completed(request.user,
                               parse_date(request.POST.get('on') or '') or None, note)
        else:
            hr_actions[action](request.user, note)
        messages.success(request, 'Request updated.')

    elif action == 'resubmit':
        if request.user != req.employee or not req.employee_can_edit:
            messages.error(request, 'This request cannot be resubmitted.')
            return redirect('training_detail', pk=pk)
        _save_fields(request, req)
        errors = _validate(request)
        if errors:
            for msg in errors.values():
                messages.error(request, msg)
            return redirect('training_detail', pk=pk)
        req.save()
        for upload in request.FILES.getlist('attachments'):
            TrainingAttachment.objects.create(
                request=req, file=upload, uploaded_by=request.user)
        req.resubmit(request.user)
        messages.success(request, 'Request updated and resubmitted.')

    elif action == 'comment':
        if note:
            req.log(request.user, 'commented', note)
            # Keep the other side of the conversation informed.
            for who in {req.employee, req.manager, req.hr_owner} - {request.user}:
                if who:
                    req.notify(who, 'New comment on a training request', note)
            messages.success(request, 'Comment added.')

    elif action == 'hr_upload' and user_is_hr(request.user):
        for upload in request.FILES.getlist('attachments'):
            TrainingAttachment.objects.create(
                request=req, file=upload, uploaded_by=request.user,
                kind=TrainingAttachment.Kind.HR)
        messages.success(request, 'HR document attached.')

    return redirect('training_detail', pk=pk)
