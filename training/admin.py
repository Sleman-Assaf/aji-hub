# training/admin.py
from django.contrib import admin

from .models import TrainingAttachment, TrainingEvent, TrainingRequest


class TrainingAttachmentInline(admin.TabularInline):
    model = TrainingAttachment
    extra = 0
    fields = ('file', 'caption', 'kind', 'uploaded_by', 'uploaded_at')
    readonly_fields = ('uploaded_at',)


class TrainingEventInline(admin.TabularInline):
    """Audit trail — read-only, so history cannot be quietly rewritten."""
    model = TrainingEvent
    extra = 0
    fields = ('created_at', 'actor', 'action', 'note')
    readonly_fields = fields
    can_delete = False
    ordering = ('created_at',)

    def has_add_permission(self, request, obj):
        return False


@admin.register(TrainingRequest)
class TrainingRequestAdmin(admin.ModelAdmin):
    list_display = ('course_name', 'employee', 'stage', 'manager',
                    'estimated_cost', 'budget_amount', 'created_at')
    list_filter = ('stage', 'created_at')
    search_fields = ('course_name', 'provider', 'employee__first_name',
                     'employee__last_name', 'employee__username')
    readonly_fields = ('created_at', 'updated_at', 'manager_decided_at',
                       'hr_decided_at')
    inlines = [TrainingAttachmentInline, TrainingEventInline]
    fieldsets = (
        ('Request', {'fields': ('employee', 'course_name', 'provider',
                                'business_need', 'justification',
                                'expected_benefits', 'comments')}),
        ('Cost and dates', {'fields': ('estimated_cost', 'currency',
                                       'preferred_from', 'preferred_to')}),
        ('Workflow', {'fields': ('stage', 'manager', 'manager_decided_at',
                                 'manager_comment')}),
        ('HR', {'fields': ('hr_owner', 'hr_decided_at', 'hr_notes',
                           'budget_amount', 'scheduled_on', 'completed_on')}),
        ('Timestamps', {'fields': ('created_at', 'updated_at'),
                        'classes': ('collapse',)}),
    )
