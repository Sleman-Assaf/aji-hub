# cafeteria/models.py
from decimal import Decimal

from django.contrib.auth.models import User
from django.db import models


STAFF_GROUP = 'Cafeteria Staff'
FACILITY_GROUP = 'Facility Managers'


def is_cafeteria_staff(user):
    return user.is_superuser or user.groups.filter(name=STAFF_GROUP).exists()


def is_facility_manager(user):
    return user.is_superuser or user.groups.filter(name=FACILITY_GROUP).exists()


class MenuCategory(models.Model):
    """A section of the cafeteria menu (Hot Coffee, Fresh Juices, Cookies...)."""
    name = models.CharField(max_length=100)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'name']
        verbose_name_plural = 'Menu categories'

    def __str__(self):
        return self.name


class MenuItem(models.Model):
    """A drink or snack employees can order from the cafeteria."""
    category = models.ForeignKey(MenuCategory, on_delete=models.CASCADE, related_name='items')
    name = models.CharField(max_length=150)
    description = models.CharField(max_length=255, blank=True)
    price = models.DecimalField(max_digits=7, decimal_places=2)
    image = models.ImageField(upload_to='cafeteria/', blank=True, null=True,
                              help_text='Item photo shown to employees. Managed by cafeteria staff.')
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['category__order', 'order', 'name']

    def __str__(self):
        return f'{self.name} ({self.price})'


class CafeteriaOrder(models.Model):
    """An individual employee order from the cafeteria."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PREPARING = 'preparing', 'Preparing'
        DELIVERED = 'delivered', 'Delivered'
        CANCELLED = 'cancelled', 'Cancelled'

    ordered_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='cafeteria_orders')
    delivery_location = models.CharField(max_length=200, blank=True, help_text='Desk / floor / meeting room')
    notes = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Order #{self.pk} — {self.ordered_by.get_full_name()} ({self.get_status_display()})'

    def total(self):
        return sum((li.unit_price * li.quantity for li in self.lines.all()), Decimal('0'))

    def summary(self):
        return ', '.join(f'{li.quantity}× {li.item_name}' for li in self.lines.all())


class CafeteriaOrderItem(models.Model):
    """A line in an employee order. Name/price are snapshotted at order time."""
    order = models.ForeignKey(CafeteriaOrder, on_delete=models.CASCADE, related_name='lines')
    item = models.ForeignKey(MenuItem, on_delete=models.SET_NULL, null=True, blank=True)
    item_name = models.CharField(max_length=150)
    unit_price = models.DecimalField(max_digits=7, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f'{self.quantity}× {self.item_name}'


class MeetingPackage(models.Model):
    """A catering package for AJi meetings, paid by AJi (not the visitor).
    Managed by cafeteria staff, ordered by Facility Managers according to the
    level of the meeting attendees."""

    class Level(models.TextChoices):
        VIP = 'vip', 'VIP'
        REFRESHMENT = 'refreshment', 'Refreshment'
        DRINKS = 'drinks', 'Drinks'
        BREAKFAST = 'breakfast', 'Breakfast'
        LUNCH = 'lunch', 'Lunch'

    name = models.CharField(max_length=150)
    level = models.CharField(max_length=15, choices=Level.choices)
    description = models.TextField(blank=True, help_text='What the package includes')
    price_per_person = models.DecimalField(max_digits=7, decimal_places=2)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return f'{self.name} — {self.get_level_display()} ({self.price_per_person}/person)'


class PackageOrder(models.Model):
    """A meeting package ordered by a Facility Manager, billed to AJi."""

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PREPARING = 'preparing', 'Preparing'
        DELIVERED = 'delivered', 'Delivered'
        CANCELLED = 'cancelled', 'Cancelled'

    package = models.ForeignKey(MeetingPackage, on_delete=models.PROTECT, related_name='orders')
    ordered_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='package_orders')
    meeting_title = models.CharField(max_length=200)
    meeting_room = models.CharField(max_length=150, blank=True)
    meeting_date = models.DateField()
    meeting_time = models.TimeField()
    attendees_count = models.PositiveIntegerField(default=1)
    notes = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    paid_by = models.CharField(max_length=50, default='AJi', editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.package.name} for "{self.meeting_title}" ({self.get_status_display()})'

    def total(self):
        return self.package.price_per_person * self.attendees_count


class CateringRequest(models.Model):
    """Catering requested with a meeting-room reservation. The room booking is
    confirmed immediately; the catering goes to the Facility Manager for
    approval (they can modify the package/attendees, approve or reject). On
    approval a PackageOrder is placed to the cafeteria staff, paid by AJi."""

    class Status(models.TextChoices):
        WAITING_ROOM = 'waiting_room', 'Waiting for Room Approval'
        PENDING = 'pending', 'Pending Facility Manager Approval'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    booking = models.OneToOneField(
        'services.RoomBooking', on_delete=models.CASCADE, related_name='catering_request',
    )
    package = models.ForeignKey(MeetingPackage, on_delete=models.PROTECT, related_name='catering_requests')
    attendees_count = models.PositiveIntegerField(default=1)
    notes = models.CharField(max_length=255, blank=True)
    requested_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='catering_requests')
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='catering_requests_reviewed',
    )
    review_comment = models.CharField(max_length=255, blank=True)
    package_order = models.OneToOneField(
        PackageOrder, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='catering_request', help_text='Created when the Facility Manager approves.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Catering for booking #{self.booking_id} — {self.package.name} ({self.get_status_display()})'

    def total(self):
        return self.package.price_per_person * self.attendees_count
