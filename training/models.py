# training/models.py
"""Training Expression of Interest, with a two-stage approval chain.

    Employee  ->  Direct manager  ->  HR  ->  Scheduled / Completed

Every transition goes through a method on TrainingRequest so the stage change,
the audit event and the notification always happen together — there is no way
to move a request without leaving a trail.
"""
from django.contrib.auth.models import Group, User
from django.db import models
from django.utils import timezone

HR_APPROVER_GROUP = 'HR'


class TrainingRequest(models.Model):
    class Stage(models.TextChoices):
        PENDING_MANAGER = 'pending_manager', 'Pending Manager Approval'
        RETURNED        = 'returned',        'Returned for Changes'
        PENDING_HR      = 'pending_hr',      'Pending HR Approval'
        HR_INFO         = 'hr_info',         'More Information Requested'
        APPROVED        = 'approved',        'Approved'
        SCHEDULED       = 'scheduled',       'Scheduled'
        COMPLETED       = 'completed',       'Completed'
        REJECTED        = 'rejected',        'Rejected'

    #: Stages the employee can still edit and resubmit from.
    EDITABLE_STAGES = {Stage.RETURNED, Stage.HR_INFO}
    #: Stages where nothing further will happen.
    CLOSED_STAGES = {Stage.REJECTED, Stage.COMPLETED}

    employee = models.ForeignKey(User, on_delete=models.CASCADE,
                                related_name='training_requests')

    # ── what is being asked for ──────────────────────────────────────────
    course_name       = models.CharField(max_length=250)
    provider          = models.CharField(max_length=200, blank=True)
    business_need     = models.TextField(
        help_text='Why is this training needed?')
    justification     = models.TextField(
        help_text='Business justification — what happens if it does not go ahead?')
    expected_benefits = models.TextField(blank=True)
    comments          = models.TextField(blank=True)
    estimated_cost    = models.DecimalField(max_digits=10, decimal_places=2,
                                            null=True, blank=True)
    currency          = models.CharField(max_length=8, default='JOD')
    preferred_from    = models.DateField(null=True, blank=True)
    preferred_to      = models.DateField(null=True, blank=True)

    # ── routing and decisions ────────────────────────────────────────────
    stage = models.CharField(max_length=20, choices=Stage.choices,
                             default=Stage.PENDING_MANAGER)

    manager             = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='training_to_approve',
        help_text="Resolved from the employee's reporting line when submitted.")
    manager_decided_at  = models.DateTimeField(null=True, blank=True)
    manager_comment     = models.TextField(blank=True)

    hr_owner        = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='training_hr_owned')
    hr_decided_at   = models.DateTimeField(null=True, blank=True)
    hr_notes        = models.TextField(blank=True)
    budget_amount   = models.DecimalField(max_digits=10, decimal_places=2,
                                          null=True, blank=True,
                                          help_text='Budget HR has allocated.')

    scheduled_on = models.DateField(null=True, blank=True)
    completed_on = models.DateField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Training request'

    def __str__(self):
        return f'{self.course_name} — {self.employee.get_full_name() or self.employee.username}'

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('training_detail', args=[self.pk])

    # ── derived state ────────────────────────────────────────────────────
    @property
    def is_open(self):
        return self.stage not in self.CLOSED_STAGES

    @property
    def employee_can_edit(self):
        return self.stage in self.EDITABLE_STAGES

    @property
    def awaiting(self):
        """Plain-English label for who the ball is with."""
        return {
            self.Stage.PENDING_MANAGER: 'Your manager',
            self.Stage.PENDING_HR: 'HR',
            self.Stage.RETURNED: 'You — changes requested',
            self.Stage.HR_INFO: 'You — more information needed',
            self.Stage.APPROVED: 'HR to schedule',
            self.Stage.SCHEDULED: 'Attend the training',
            self.Stage.COMPLETED: 'Nothing — completed',
            self.Stage.REJECTED: 'Nothing — rejected',
        }.get(self.stage, '')

    @property
    def pill_class(self):
        """Maps onto the Hub's existing .pill variants."""
        if self.stage in (self.Stage.APPROVED, self.Stage.COMPLETED):
            return 'ok'
        if self.stage == self.Stage.REJECTED:
            return 'bad'
        if self.stage in (self.Stage.RETURNED, self.Stage.HR_INFO):
            return 'warn'
        if self.stage == self.Stage.SCHEDULED:
            return 'info'
        return 'mut'

    # ── permissions ──────────────────────────────────────────────────────
    def can_view(self, user):
        from core.models import user_is_hr
        return (user.is_superuser or user_is_hr(user)
                or user == self.employee or user == self.manager)

    def can_act_as_manager(self, user):
        return (self.stage == self.Stage.PENDING_MANAGER
                and (user == self.manager or user.is_superuser))

    def can_act_as_hr(self, user):
        from core.models import user_is_hr
        return user_is_hr(user) and self.stage in (
            self.Stage.PENDING_HR, self.Stage.APPROVED, self.Stage.SCHEDULED)

    # ── transitions ──────────────────────────────────────────────────────
    def resolve_manager(self):
        """The employee's direct manager, via the employee tree."""
        try:
            mgr_employee = self.employee.employee.manager
        except Exception:
            return None
        return mgr_employee.user if mgr_employee else None

    def submit(self, actor=None):
        self.manager = self.resolve_manager()
        # With no manager on record the request goes straight to HR rather than
        # stalling in a stage nobody can action.
        self.stage = (self.Stage.PENDING_MANAGER if self.manager
                      else self.Stage.PENDING_HR)
        self.save()
        self.log(actor or self.employee, 'submitted',
                 'Expression of interest submitted.')
        self.notify(self.employee, 'Training request submitted',
                    f'Your request for “{self.course_name}” has been submitted.')
        if self.manager:
            self.notify(self.manager, 'Training request needs your approval',
                        f'{self._who()} asked to attend “{self.course_name}”.')
        else:
            self._notify_hr('Training request needs HR approval',
                            f'{self._who()} asked to attend “{self.course_name}”. '
                            'No manager is on record, so it came straight to HR.')
        return self

    def manager_approve(self, actor, note=''):
        self.stage = self.Stage.PENDING_HR
        self.manager_decided_at = timezone.now()
        self.manager_comment = note
        self.save()
        self.log(actor, 'manager_approved', note or 'Approved by manager.')
        self.notify(self.employee, 'Manager approved your training request',
                    f'“{self.course_name}” has gone to HR for review.')
        self._notify_hr('Training request needs HR approval',
                        f'{self._who()} — “{self.course_name}” was approved by their manager.')
        return self

    def manager_reject(self, actor, note=''):
        self.stage = self.Stage.REJECTED
        self.manager_decided_at = timezone.now()
        self.manager_comment = note
        self.save()
        self.log(actor, 'manager_rejected', note or 'Rejected by manager.')
        self.notify(self.employee, 'Manager declined your training request',
                    note or f'“{self.course_name}” was not approved.')
        return self

    def manager_return(self, actor, note=''):
        self.stage = self.Stage.RETURNED
        self.manager_comment = note
        self.save()
        self.log(actor, 'returned', note or 'Returned for changes.')
        self.notify(self.employee, 'Training request returned for changes',
                    note or f'Your manager asked for changes to “{self.course_name}”.')
        return self

    def hr_approve(self, actor, note='', budget=None):
        self.stage = self.Stage.APPROVED
        self.hr_owner = actor
        self.hr_decided_at = timezone.now()
        if note:
            self.hr_notes = note
        if budget is not None:
            self.budget_amount = budget
        self.save()
        self.log(actor, 'hr_approved', note or 'Approved by HR.')
        self.notify(self.employee, 'HR approved your training request',
                    f'“{self.course_name}” is approved. HR will confirm dates.')
        return self

    def hr_reject(self, actor, note=''):
        self.stage = self.Stage.REJECTED
        self.hr_owner = actor
        self.hr_decided_at = timezone.now()
        if note:
            self.hr_notes = note
        self.save()
        self.log(actor, 'hr_rejected', note or 'Rejected by HR.')
        self.notify(self.employee, 'HR declined your training request',
                    note or f'“{self.course_name}” was not approved.')
        return self

    def hr_request_info(self, actor, note=''):
        self.stage = self.Stage.HR_INFO
        self.hr_owner = actor
        if note:
            self.hr_notes = note
        self.save()
        self.log(actor, 'hr_info', note or 'HR asked for more information.')
        self.notify(self.employee, 'HR needs more information',
                    note or f'HR asked for more detail on “{self.course_name}”.')
        return self

    def mark_scheduled(self, actor, on=None, note=''):
        self.stage = self.Stage.SCHEDULED
        self.scheduled_on = on
        self.save()
        when = f' for {on:%d %b %Y}' if on else ''
        self.log(actor, 'scheduled', note or f'Training scheduled{when}.')
        self.notify(self.employee, 'Your training is scheduled',
                    f'“{self.course_name}” is scheduled'
                    + (f' for {on:%d %b %Y}.' if on else '.'))
        return self

    def mark_completed(self, actor, on=None, note=''):
        self.stage = self.Stage.COMPLETED
        self.completed_on = on or timezone.now().date()
        self.save()
        self.log(actor, 'completed', note or 'Training completed.')
        self.notify(self.employee, 'Training marked complete',
                    f'“{self.course_name}” has been marked complete. Well done.')
        return self

    def resubmit(self, actor):
        """Employee sends an amended request back into the chain."""
        back_to_hr = self.stage == self.Stage.HR_INFO
        self.stage = self.Stage.PENDING_HR if back_to_hr else self.Stage.PENDING_MANAGER
        self.save()
        self.log(actor, 'resubmitted', 'Updated and resubmitted.')
        if back_to_hr:
            self._notify_hr('Updated training request',
                            f'{self._who()} updated “{self.course_name}”.')
        elif self.manager:
            self.notify(self.manager, 'Updated training request needs approval',
                        f'{self._who()} updated “{self.course_name}”.')
        return self

    # ── plumbing ─────────────────────────────────────────────────────────
    def _who(self):
        return self.employee.get_full_name() or self.employee.username

    def log(self, actor, action, note=''):
        return TrainingEvent.objects.create(
            request=self, actor=actor, action=action, note=note)

    def notify(self, user, title, body=''):
        """One Hub notification, honouring the recipient's preferences."""
        if not user:
            return None
        from core.models import Notification
        return Notification.objects.create(
            recipient=user, type=Notification.Type.INFO,
            title=title, body=body[:500], link=self.get_absolute_url())

    def _notify_hr(self, title, body=''):
        recipients = User.objects.filter(
            models.Q(groups__name=HR_APPROVER_GROUP)
            | models.Q(profile__role__in=['hr', 'super_admin'])
        ).distinct()
        for user in recipients:
            self.notify(user, title, body)


class TrainingAttachment(models.Model):
    class Kind(models.TextChoices):
        SUPPORTING = 'supporting', 'Supporting document'
        HR         = 'hr',         'HR document'

    request = models.ForeignKey(TrainingRequest, on_delete=models.CASCADE,
                               related_name='attachments')
    file = models.FileField(upload_to='training/')
    caption = models.CharField(max_length=250, blank=True)
    kind = models.CharField(max_length=12, choices=Kind.choices,
                            default=Kind.SUPPORTING)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL,
                                    null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']

    def __str__(self):
        return self.filename

    @property
    def filename(self):
        return self.file.name.rsplit('/', 1)[-1] if self.file else ''


class TrainingEvent(models.Model):
    """Immutable audit trail — one row per action, rendered as the timeline."""
    ACTION_LABELS = {
        'submitted':        'Submitted',
        'manager_approved': 'Manager approved',
        'manager_rejected': 'Manager rejected',
        'returned':         'Returned for changes',
        'resubmitted':      'Resubmitted',
        'hr_approved':      'HR approved',
        'hr_rejected':      'HR rejected',
        'hr_info':          'HR requested information',
        'scheduled':        'Scheduled',
        'completed':        'Completed',
        'commented':        'Comment',
    }

    request = models.ForeignKey(TrainingRequest, on_delete=models.CASCADE,
                               related_name='events')
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=30)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'pk']

    def __str__(self):
        return f'{self.label} — {self.request_id}'

    @property
    def label(self):
        return self.ACTION_LABELS.get(self.action, self.action.replace('_', ' ').title())
