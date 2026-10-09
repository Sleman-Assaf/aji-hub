# services/admin.py
from django.contrib import admin

from .models import ApprovalLog, JobRequisition, MeetingRoom, RoomBooking, Service, ServiceCategory


class ServiceInline(admin.TabularInline):
    model = Service
    extra = 0
    fields = ('name', 'description', 'group', 'service_key', 'icon', 'order', 'is_active')


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'category_type', 'order', 'is_active')
    list_filter = ('category_type', 'is_active')
    search_fields = ('name',)
    inlines = [ServiceInline]


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'group', 'service_key', 'order', 'is_active')
    list_filter = ('category', 'group', 'is_active')
    search_fields = ('name', 'service_key')


@admin.register(MeetingRoom)
class MeetingRoomAdmin(admin.ModelAdmin):
    list_display = ('name', 'chairs', 'order', 'is_active')
    ordering = ('order', 'name')


@admin.register(RoomBooking)
class RoomBookingAdmin(admin.ModelAdmin):
    list_display = ('room', 'date', 'start_time', 'end_time', 'booked_by', 'title')
    list_filter = ('room', 'date')
    search_fields = ('title', 'booked_by__username')
    date_hierarchy = 'date'


class ApprovalLogInline(admin.TabularInline):
    model = ApprovalLog
    extra = 0
    readonly_fields = ('stage', 'actor', 'decision', 'comment', 'acted_at')
    can_delete = False


@admin.register(JobRequisition)
class JobRequisitionAdmin(admin.ModelAdmin):
    list_display = ('job_title', 'company', 'department', 'requested_by', 'status', 'director', 'hr_approver', 'ceo_approver', 'created_at')
    list_filter = ('status', 'company', 'department')
    search_fields = ('job_title', 'department__name', 'requested_by__username')
    inlines = [ApprovalLogInline]


@admin.register(ApprovalLog)
class ApprovalLogAdmin(admin.ModelAdmin):
    list_display = ('requisition', 'stage', 'actor', 'decision', 'acted_at')


# ── the fleet ─────────────────────────────────────────────────────────────
# The Bookings desk runs all of this from the Hub itself; these are here for
# an Intranet Admin who needs to correct something in bulk, or look at a row
# the interface does not show.
from .models import (FleetApprover, Place, Vehicle, VehicleAssignment,
                     VehicleBooking, VehicleEvent, VehicleTripLog)


class VehicleEventInline(admin.TabularInline):
    model = VehicleEvent
    extra = 0
    fields = ('kind', 'happened_on', 'title', 'cost', 'currency', 'is_settled')


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ('label', 'plate', 'seats', 'status', 'odometer',
                    'insurance_expiry', 'registration_expiry', 'next_service_due')
    list_filter = ('entity', 'status')
    search_fields = ('make', 'model', 'plate')
    inlines = [VehicleEventInline]


class VehicleAssignmentInline(admin.TabularInline):
    model = VehicleAssignment
    extra = 0
    autocomplete_fields = ('vehicle',)


@admin.register(VehicleBooking)
class VehicleBookingAdmin(admin.ModelAdmin):
    list_display = ('date', 'departure_time', 'entity', 'booked_by',
                    'to_address', 'status', 'reviewed_by')
    list_filter = ('status', 'entity', 'date')
    search_fields = ('from_address', 'to_address', 'purpose',
                     'booked_by__username')
    date_hierarchy = 'date'
    inlines = [VehicleAssignmentInline]
    readonly_fields = ('created_at', 'updated_at', 'calendar_event')


@admin.register(VehicleEvent)
class VehicleEventAdmin(admin.ModelAdmin):
    list_display = ('vehicle', 'kind', 'happened_on', 'title', 'cost',
                    'is_settled')
    list_filter = ('kind', 'is_settled', 'vehicle__entity')
    search_fields = ('title', 'reference', 'vendor')
    date_hierarchy = 'happened_on'


@admin.register(VehicleTripLog)
class VehicleTripLogAdmin(admin.ModelAdmin):
    list_display = ('vehicle', 'left_at', 'returned_at', 'driver',
                    'odometer_out', 'odometer_in')
    list_filter = ('vehicle__entity', 'vehicle')
    date_hierarchy = 'left_at'


@admin.register(FleetApprover)
class FleetApproverAdmin(admin.ModelAdmin):
    """Who signs off which division's cars. A division with no row here falls
    back to the whole Bookings group, so a request never lands in no queue."""
    list_display = ('entity', 'user', 'notify', 'order')
    list_filter = ('entity', 'notify')
    autocomplete_fields = ('user',)
    ordering = ('entity__code', 'order')


@admin.register(Place)
class PlaceAdmin(admin.ModelAdmin):
    list_display = ('name', 'kind', 'entity', 'is_default_origin', 'is_active')
    list_filter = ('kind', 'entity', 'is_active')
    search_fields = ('name', 'address')
