# services/models.py
from django.db import models
from django.contrib.auth.models import Group, User

from people.models import Department, Employee, Entity


class ServiceCategory(models.Model):
    """A main category on the My AJi page (HR, Finance & Expenses, IT & Tech
    Support, Workplace & Facilities, Approvals...). Admin-manageable."""

    class CategoryType(models.TextChoices):
        SERVICES = 'services', 'Services (lists sub-services)'
        APPROVALS = 'approvals', 'Approvals (links straight to the approvals inbox)'

    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    icon = models.CharField(max_length=30, blank=True, help_text="Icon key, e.g. 'hr', 'finance', 'it', 'facilities', 'approvals'")
    category_type = models.CharField(max_length=20, choices=CategoryType.choices, default=CategoryType.SERVICES)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'name']
        verbose_name_plural = 'Service categories'

    def __str__(self):
        return self.name


class Service(models.Model):
    """A sub-category / service under a ServiceCategory (e.g. 'Job Requisition'
    under HR). Visible only to members of `group`. Admin-manageable.

    `service_key` links this DB row to the actual feature's code (list of past
    requests + 'new' form) via services.service_registry.SERVICE_HANDLERS.
    Categories/services can be created ahead of the feature being built —
    they'll just show a 'coming soon' state until a handler is registered.
    """

    category = models.ForeignKey(ServiceCategory, on_delete=models.CASCADE, related_name='services')
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    group = models.ForeignKey(
        Group, on_delete=models.SET_NULL, null=True, blank=True,
        help_text='Permission group required to access this service. Leave blank to allow everyone.',
    )
    service_key = models.SlugField(
        max_length=60, unique=True,
        help_text="Internal key matching an entry in services.service_registry.SERVICE_HANDLERS, e.g. 'job_requisition'.",
    )
    icon = models.CharField(max_length=30, blank=True)
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return f'{self.name} ({self.category.name})'

    def user_has_access(self, user):
        """May this person open this service?

        Group membership, plus the administrator bypass every other permission
        rule in the Hub already has (see core.models.user_can_manage_* and
        is_intranet_admin). Without it, a superuser in Intranet Admin was
        refused Job Requisition and Meeting Packages outright — an administrator
        could neither see the form nor check that it worked, which is the one
        person who has to be able to.
        """
        if not self.group_id:
            return True
        from core.models import is_intranet_admin
        if is_intranet_admin(user):
            return True
        return user.groups.filter(pk=self.group_id).exists()


class JobRequisition(models.Model):
    """A 'New Position / Job Requisition' request, mirroring the AJi paper form.

    Approval chain: Hiring Manager (requester) -> Director (requester's manager)
    -> HR Manager (any 'HR Managers' group member) -> CEO (any 'CEO' group member).
    """

    class Status(models.TextChoices):
        PENDING_DIRECTOR = 'pending_director', 'Pending Director Approval'
        PENDING_HR = 'pending_hr', 'Pending HR Approval'
        PENDING_CEO = 'pending_ceo', 'Pending CEO Approval'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    # New Position Details
    requested_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='job_requisitions')
    job_title = models.CharField(max_length=200)
    company = models.ForeignKey(
        Entity, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='job_requisitions', help_text='Company / entity this position belongs to.',
    )
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='job_requisitions', help_text='Department / unit this position belongs to.',
    )
    reports_to = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='job_requisitions_reported_to',
        help_text='Head / manager of the department this position reports to.',
    )
    date_of_request = models.DateField(auto_now_add=True)
    targeted_start_date = models.DateField(null=True, blank=True)
    work_location = models.CharField(max_length=200, blank=True)

    # Justification
    business_need = models.TextField(help_text='Why is this position required, and what are the risks if not created?')

    # Position Details
    primary_responsibilities = models.TextField(blank=True)
    key_qualifications = models.TextField(blank=True)

    # Approval chain state
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING_DIRECTOR)
    director = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='requisitions_to_approve_as_director',
        help_text="Requester's direct manager, resolved at submission time.",
    )
    hr_approver = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='requisitions_approved_as_hr',
    )
    ceo_approver = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='requisitions_approved_as_ceo',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.job_title} ({self.get_status_display()})'

    def is_final(self):
        return self.status in (self.Status.APPROVED, self.Status.REJECTED)

    def stage_label(self):
        return self.get_status_display()


class ApprovalLog(models.Model):
    class Decision(models.TextChoices):
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    requisition = models.ForeignKey(JobRequisition, on_delete=models.CASCADE, related_name='logs')
    stage = models.CharField(max_length=20)
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    decision = models.CharField(max_length=10, choices=Decision.choices)
    comment = models.TextField(blank=True)
    acted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['acted_at']

    def __str__(self):
        return f'{self.requisition} — {self.get_decision_display()} by {self.actor}'


class MeetingRoom(models.Model):
    """One of the bookable meeting rooms."""
    name = models.CharField(max_length=100)
    chairs = models.PositiveIntegerField(help_text='Seating capacity')
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return f'{self.name} ({self.chairs} chairs)'


#: Kept under its old name because views, seeds and templates across the
#: project import it. The desk was renamed, not replaced — it now runs the cars
#: as well as the rooms — so this points at the new group rather than at a
#: group that no longer exists.
RECEPTION_GROUP = 'Bookings'
#: What it used to be called, for any database that has not been through
#: `setup_fleet` yet.
LEGACY_RECEPTION_GROUP = 'Reception Desk'


class RoomBooking(models.Model):
    """A single booked half-hour-aligned time range for a meeting room.
    Bookings are requests: they go to the Reception Desk for approval —
    the receptionist can approve, reject, or modify the room/time then approve."""

    class MeetingType(models.TextChoices):
        INTERNAL = 'internal', 'Internal'
        EXTERNAL = 'external', 'External'

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending Reception Approval'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'

    # Optional, because a meeting is not always in a room. A call across
    # countries has attendees, a time and a Teams link and nothing to reserve,
    # and making it invent a room to exist would put a phantom booking on a
    # real room's schedule.
    room = models.ForeignKey(MeetingRoom, on_delete=models.CASCADE,
                             related_name='bookings', null=True, blank=True)
    booked_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='room_bookings')
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    title = models.CharField(max_length=200, blank=True, help_text='Meeting subject (optional)')
    meeting_type = models.CharField(max_length=10, choices=MeetingType.choices, default=MeetingType.INTERNAL)
    attendees_count = models.PositiveIntegerField(null=True, blank=True, help_text='How many people will attend')
    catering_requirements = models.TextField(blank=True)
    it_requirements = models.TextField(blank=True)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='room_bookings_reviewed', help_text='Reception desk employee who reviewed this request.',
    )
    review_comment = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    #: What the meeting is about, for the people invited to it. Becomes the
    #: `body` of the Outlook invitation when the Hub can send one.
    description = models.TextField(blank=True)

    #: Needs a Teams link. A meeting can be both: a room for the people in
    #: Amman and a link for the ones who are not, which is the ordinary case
    #: for anything across offices.
    is_online = models.BooleanField(
        default=False, verbose_name='Teams meeting',
        help_text='Also hold this on Microsoft Teams.')
    #: Filled in by Graph when it creates the online meeting. Until then it can
    #: be pasted in by hand, so a Teams meeting is usable before the
    #: integration exists rather than after it.
    online_url = models.URLField(blank=True, max_length=500)

    class Meta:
        ordering = ['date', 'start_time']

    def __str__(self):
        return f'{self.where} — {self.date} {self.start_time}-{self.end_time} ({self.booked_by})'

    @property
    def where(self):
        """Where this meeting happens, in words. One place to ask.

        The room used to be compulsory, so everything from notification text to
        the catering screens read `booking.room.name` directly. Now that a
        meeting can be online only, that would be an AttributeError in a dozen
        places — so they ask this instead.
        """
        room = self.room.name if self.room_id else ''
        if room and self.is_online:
            return f'{room} + Teams'
        if room:
            return room
        return 'Microsoft Teams' if self.is_online else 'No room'

    @property
    def needs_reception(self):
        """Does a person have to look at this before it is real?

        A free room taken by an employee for an internal meeting is not a
        decision anybody needs to make, and holding it "pending" made the
        calendar lie about what was booked. Reception still decides where there
        is something to decide: an external meeting, or catering to arrange.
        """
        if self.meeting_type == self.MeetingType.EXTERNAL:
            return True
        return bool(self.catering_requirements) or hasattr(self, 'catering_request')

    def overlaps(self, other_start, other_end):
        return self.start_time < other_end and other_start < self.end_time

    # ── who is coming ────────────────────────────────────────────────────
    # attendees_count above is a number somebody types in, and it is what the
    # catering package is priced against. These are the actual people, which is
    # a different question: a meeting can be booked for twelve and invite four.

    @property
    def required_invites(self):
        return [i for i in self.invites.all() if i.kind == BookingInvite.Kind.REQUIRED]

    @property
    def optional_invites(self):
        return [i for i in self.invites.all() if i.kind == BookingInvite.Kind.OPTIONAL]

    @property
    def response_summary(self):
        """"3 accepted · 1 declined · 2 awaiting" — blank when nobody is invited."""
        invites = list(self.invites.all())
        if not invites:
            return ''
        counts = {}
        for i in invites:
            counts[i.response] = counts.get(i.response, 0) + 1
        order = [BookingInvite.Response.ACCEPTED, BookingInvite.Response.DECLINED,
                 BookingInvite.Response.PENDING]
        labels = {BookingInvite.Response.ACCEPTED: 'accepted',
                  BookingInvite.Response.DECLINED: 'declined',
                  BookingInvite.Response.PENDING: 'awaiting'}
        return ' · '.join(f'{counts[r]} {labels[r]}' for r in order if counts.get(r))


class BookingInvite(models.Model):
    """One person asked to a meeting, and what they said.

    A row per person rather than two lists on the booking, because the answer
    belongs to the person: "required" is what the organiser asked of them, and
    "accepted" is what they replied, and those are separate facts that change at
    separate times.

    Today an invitation is delivered in the Hub only. The organiser, the
    required and optional split, and the response are exactly what Microsoft
    Graph wants in an `attendees[]` array, so when the Hub can act as the signed
    in user this becomes the thing it sends rather than something to migrate.
    """
    class Kind(models.TextChoices):
        REQUIRED = 'required', 'Required'
        OPTIONAL = 'optional', 'Optional'

    class Response(models.TextChoices):
        PENDING  = 'pending',  'Awaiting a reply'
        ACCEPTED = 'accepted', 'Accepted'
        DECLINED = 'declined', 'Declined'

    booking = models.ForeignKey(RoomBooking, on_delete=models.CASCADE, related_name='invites')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='meeting_invites')
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.REQUIRED)
    response = models.CharField(max_length=10, choices=Response.choices, default=Response.PENDING)
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # One invitation per person per meeting. Being asked twice is not a
        # thing that can happen, so it is not a state the rest has to handle.
        constraints = [
            models.UniqueConstraint(fields=['booking', 'user'], name='one_invite_per_person'),
        ]
        ordering = ['kind', 'pk']

    def __str__(self):
        return f'{self.user} — {self.get_kind_display().lower()} at {self.booking}'


# ══════════════════════════════════════════════════════════════════════════
#  THE FLEET
#
#  Booking a company car used to be an email to the Reception Desk with a six
#  column table pasted into it: date, day, departure time, location, driver
#  yes/no, attendees. That table is the shape of VehicleBooking below, with two
#  changes that the email could not make.
#
#  A LOCATION IS TWO PLACES, NOT ONE. "Location" in the email meant wherever
#  they were going; where they were leaving from was assumed to be the office
#  and often was not. Reception cannot judge whether one car can do two trips
#  without knowing where each one starts, so from and to are both required and
#  both carry coordinates as well as words — the words are what a person reads,
#  the coordinates are what a map draws.
#
#  A TRIP HAS A LENGTH. The email gave a departure time and nothing else, so
#  nothing could be said about whether a car was free at four o'clock. Every
#  booking now carries how long it is expected to take, and the return is
#  computed from it: one number to fill in, and a real end to compare against.
# ══════════════════════════════════════════════════════════════════════════

#: The people who run both booking systems — the fleet and the meeting rooms.
#: Named for what the job is rather than where the desk is, because it is no
#: longer only reception who do it.
BOOKINGS_GROUP = 'Bookings'


class Vehicle(models.Model):
    """One company car.

    Belongs to an entity — AJiBE, AJiWE, HDP, AJi Ltd — because the fleet is
    not pooled: a car is that company's asset and its running costs land on
    that company's books. The booking form only ever offers the cars of the
    division the trip is being made for.
    """

    class Status(models.TextChoices):
        ACTIVE = 'active', 'In service'
        MAINTENANCE = 'maintenance', 'In for maintenance'
        RETIRED = 'retired', 'Retired'

    entity = models.ForeignKey(Entity, on_delete=models.PROTECT,
                               related_name='vehicles',
                               help_text='Which company owns this car.')
    make = models.CharField(max_length=60, help_text='KIA, Mitsubishi, …')
    model = models.CharField(max_length=60, blank=True,
                             help_text='Sportage, Outlander, Lancer, Pickup …')
    plate = models.CharField(max_length=30, blank=True, db_index=True,
                             verbose_name='Plate number')
    seats = models.PositiveSmallIntegerField(
        default=5, help_text='Including the driver. Used to warn when a trip '
                             'has more attendees than seats.')
    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.ACTIVE)
    notes = models.TextField(blank=True)
    photo = models.ImageField(upload_to='fleet/', blank=True, null=True)

    # ── the two dates that stop a car being legal to drive ────────────────
    # Kept on the car rather than in the maintenance log because they are a
    # state, not an event: what matters is the date the cover runs out, and
    # that is one value that gets replaced, not a history to append to.
    insurer = models.CharField(max_length=120, blank=True)
    insurance_policy = models.CharField(max_length=60, blank=True)
    insurance_expiry = models.DateField(null=True, blank=True)
    registration_expiry = models.DateField(
        null=True, blank=True, verbose_name='Licence expiry')

    # ── servicing ─────────────────────────────────────────────────────────
    odometer = models.PositiveIntegerField(
        null=True, blank=True, help_text='Last reading, in kilometres.')
    next_service_due = models.DateField(null=True, blank=True)
    next_service_km = models.PositiveIntegerField(null=True, blank=True)

    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['entity__code', 'order', 'make', 'model']

    def __str__(self):
        return self.label

    @property
    def label(self):
        """'KIA Sportage — AJiWE', the way the fleet is spoken about."""
        name = ' '.join(p for p in (self.make, self.model) if p)
        return f'{name} — {self.entity.code}' if self.entity_id else name

    @property
    def is_bookable(self):
        return self.status == self.Status.ACTIVE

    # ── what is about to expire ───────────────────────────────────────────
    # One place that decides, so the fleet screen, the booking form and any
    # future reminder all agree on what "due soon" means.
    WARN_DAYS = 30

    def _due(self, when):
        from datetime import date
        if not when:
            return None
        days = (when - date.today()).days
        if days < 0:
            return ('expired', days)
        if days <= self.WARN_DAYS:
            return ('due', days)
        return ('ok', days)

    @property
    def insurance_state(self):
        return self._due(self.insurance_expiry)

    @property
    def registration_state(self):
        return self._due(self.registration_expiry)

    @property
    def service_state(self):
        return self._due(self.next_service_due)

    @property
    def alerts(self):
        """Everything wrong with this car, in words, worst first."""
        out = []
        for what, state in (('Insurance', self.insurance_state),
                            ('Licence', self.registration_state),
                            ('Service', self.service_state)):
            if not state:
                continue
            kind, days = state
            if kind == 'expired':
                out.append(( 'expired', f'{what} expired {abs(days)} days ago'))
            elif kind == 'due':
                out.append(('due', f'{what} due in {days} days'))
        if self.status == self.Status.MAINTENANCE:
            out.insert(0, ('expired', 'In for maintenance'))
        return out


class VehicleBooking(models.Model):
    """One trip: who, when, from where to where, and in which car or cars.

    A request, not a reservation. It goes to the Bookings group, who approve it,
    change it, or turn it down — the same shape as a meeting room, because it is
    the same people doing the same job.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', 'Awaiting approval'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'
        CANCELLED = 'cancelled', 'Cancelled'

    #: Live states, i.e. the ones that hold a car. Everything that asks "is this
    #: car free" filters on this, so there is one answer and not four.
    HOLDS_CAR = (Status.PENDING, Status.APPROVED)

    booked_by = models.ForeignKey(User, on_delete=models.CASCADE,
                                  related_name='vehicle_bookings')
    entity = models.ForeignKey(
        Entity, on_delete=models.PROTECT, related_name='vehicle_bookings',
        help_text='Which division the trip is for. Decides which cars are offered.')

    date = models.DateField()
    departure_time = models.TimeField()
    duration_minutes = models.PositiveIntegerField(
        default=120, help_text='How long the trip is expected to take, there and back.')

    # ── where ─────────────────────────────────────────────────────────────
    # The words and the coordinates are both kept. A person reads the words; the
    # map needs the numbers; and if the map is ever unreachable the booking is
    # still legible, which it would not be if only the pin had been stored.
    from_address = models.CharField(max_length=300)
    from_lat = models.FloatField(null=True, blank=True)
    from_lng = models.FloatField(null=True, blank=True)
    to_address = models.CharField(max_length=300)
    to_lat = models.FloatField(null=True, blank=True)
    to_lng = models.FloatField(null=True, blank=True)

    driver_required = models.BooleanField(
        default=False, help_text='Does a driver need to be assigned?')
    attendees = models.PositiveSmallIntegerField(
        default=1, help_text='How many people are travelling.')
    purpose = models.CharField(max_length=300, blank=True,
                               help_text='What the trip is for.')
    notes = models.TextField(blank=True)

    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.PENDING)
    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='vehicle_bookings_reviewed')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_comment = models.CharField(max_length=300, blank=True)

    #: The event this booking put on the company calendar, so that approving,
    #: changing or cancelling the trip can keep it in step instead of leaving
    #: an orphan on everyone's month view.
    calendar_event = models.OneToOneField(
        'content.CalendarEvent', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='vehicle_booking')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-departure_time']
        indexes = [models.Index(fields=['date', 'status'])]

    def __str__(self):
        return f'{self.date} {self.departure_time} · {self.to_address[:40]} ({self.booked_by})'

    # ── when ──────────────────────────────────────────────────────────────
    @property
    def start(self):
        from datetime import datetime
        return datetime.combine(self.date, self.departure_time)

    @property
    def end(self):
        from datetime import timedelta
        return self.start + timedelta(minutes=self.duration_minutes or 0)

    @property
    def return_time(self):
        """When the car is expected back. Computed, never stored — storing both
        a duration and an end is two facts that can disagree."""
        return self.end.time()

    @property
    def is_multi_day(self):
        """Derived, never stored. A trip is multi-day if it comes back on a
        different date, which is a fact about the two times and not a separate
        thing that could disagree with them."""
        return self.end.date() != self.date

    @property
    def return_date(self):
        return self.end.date()

    @property
    def duration_label(self):
        total = self.duration_minutes or 0
        days, rest = divmod(total, 60 * 24)
        h, m = divmod(rest, 60)
        if days:
            return f'{days}d {h}h' if h else f'{days} days'
        if h and m:
            return f'{h}h {m}m'
        return f'{h}h' if h else f'{m}m'

    def overlaps(self, other):
        """Two trips clash when one starts before the other ends.

        Compared as datetimes rather than times, because a trip that leaves at
        22:00 and runs four hours ends the next morning, and comparing bare
        clock times would call that a trip which ends before it starts.
        """
        return self.start < other.end and other.start < self.end

    @property
    def cars(self):
        return [a.vehicle for a in self.assignments.all()]

    @property
    def cars_label(self):
        names = [c.label for c in self.cars]
        return ', '.join(names) if names else 'No car assigned'

    @property
    def seats_available(self):
        return sum(c.seats for c in self.cars)

    @property
    def is_over_capacity(self):
        cars = self.cars
        return bool(cars) and self.attendees > self.seats_available

    @property
    def map_url(self):
        """An OpenStreetMap route between the two pins, for anyone who wants it
        outside the Hub. Empty unless both ends were actually placed."""
        if None in (self.from_lat, self.from_lng, self.to_lat, self.to_lng):
            return ''
        return ('https://www.openstreetmap.org/directions?engine=fossgis_osrm_car'
                f'&route={self.from_lat}%2C{self.from_lng}%3B{self.to_lat}%2C{self.to_lng}')


class VehicleAssignment(models.Model):
    """One car on one booking.

    A row rather than a ManyToMany field because a trip that takes three cars
    may not need three drivers, and the driver is a fact about this car on this
    trip — not about the car, and not about the booking.
    """
    booking = models.ForeignKey(VehicleBooking, on_delete=models.CASCADE,
                                related_name='assignments')
    vehicle = models.ForeignKey(Vehicle, on_delete=models.PROTECT,
                                related_name='assignments')
    driver = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='driving_assignments',
        help_text='Assigned by the Bookings desk, when a driver was asked for.')

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['booking', 'vehicle'],
                                    name='one_row_per_car_per_booking'),
        ]
        ordering = ['vehicle__make', 'vehicle__model']

    def __str__(self):
        return f'{self.vehicle} on {self.booking_id}'


class VehicleTripLog(models.Model):
    """What actually happened, as against what was booked.

    Separate from the booking because a booking is a plan and this is a record:
    the plan said two hours and the car came back at six, and both facts need to
    survive. It is also where the odometer comes from — the fleet's service
    intervals are in kilometres, and this is the only place kilometres are
    observed.
    """
    booking = models.ForeignKey(VehicleBooking, on_delete=models.CASCADE,
                                related_name='trip_logs', null=True, blank=True,
                                help_text='Blank for a trip that was never booked.')
    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE,
                                related_name='trip_logs')
    driver = models.ForeignKey(Employee, on_delete=models.SET_NULL,
                               null=True, blank=True, related_name='trip_logs')

    left_at = models.DateTimeField(null=True, blank=True)
    returned_at = models.DateTimeField(null=True, blank=True)
    odometer_out = models.PositiveIntegerField(null=True, blank=True)
    odometer_in = models.PositiveIntegerField(null=True, blank=True)
    fuel_cost = models.DecimalField(max_digits=8, decimal_places=2,
                                    null=True, blank=True)
    currency = models.CharField(max_length=8, default='JOD')
    notes = models.TextField(blank=True)
    logged_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                  blank=True, related_name='trip_logs_entered')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-left_at', '-created_at']

    def __str__(self):
        return f'{self.vehicle} — {self.left_at:%Y-%m-%d} ' if self.left_at else str(self.vehicle)

    @property
    def distance_km(self):
        if self.odometer_out is None or self.odometer_in is None:
            return None
        return max(0, self.odometer_in - self.odometer_out)

    @property
    def actual_minutes(self):
        if not (self.left_at and self.returned_at):
            return None
        return int((self.returned_at - self.left_at).total_seconds() // 60)

    def save(self, *args, **kwargs):
        """Keep the car's odometer current.

        The reading lives on the vehicle because that is what the next service
        is measured against, and it is only ever moved forward — a log entered
        late, out of order, must not wind the fleet's mileage back.
        """
        super().save(*args, **kwargs)
        if self.odometer_in and (self.vehicle.odometer or 0) < self.odometer_in:
            Vehicle.objects.filter(pk=self.vehicle_id).update(odometer=self.odometer_in)


class VehicleEvent(models.Model):
    """Anything that happened to a car and cost money or attention.

    One table for services, repairs, fines and accidents rather than four,
    because the fleet desk reads them as one history — "what has this car done
    this year" — and the only thing that differs between them is a label and
    whether there is a date it recurs on.
    """

    class Kind(models.TextChoices):
        SERVICE = 'service', 'Service'
        REPAIR = 'repair', 'Repair'
        INSURANCE = 'insurance', 'Insurance renewal'
        REGISTRATION = 'registration', 'Licence renewal'
        FINE = 'fine', 'Traffic fine'
        ACCIDENT = 'accident', 'Accident'
        OTHER = 'other', 'Other'

    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE,
                                related_name='events')
    kind = models.CharField(max_length=14, choices=Kind.choices,
                            default=Kind.SERVICE)
    happened_on = models.DateField()
    title = models.CharField(max_length=200)
    detail = models.TextField(blank=True)
    cost = models.DecimalField(max_digits=10, decimal_places=2,
                               null=True, blank=True)
    currency = models.CharField(max_length=8, default='JOD')
    odometer = models.PositiveIntegerField(null=True, blank=True)
    reference = models.CharField(max_length=80, blank=True,
                                 help_text='Ticket number, invoice number, policy number.')
    vendor = models.CharField(max_length=120, blank=True,
                              help_text='Garage, insurer, authority.')

    #: A fine belongs to whoever was driving. Nullable because half of them
    #: arrive months later with nothing but a plate and a timestamp on them.
    driver = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True,
                               blank=True, related_name='vehicle_events')
    is_settled = models.BooleanField(
        default=False, help_text='Paid, closed, or otherwise dealt with.')

    document = models.FileField(upload_to='fleet/docs/', blank=True, null=True)
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                     blank=True, related_name='vehicle_events_recorded')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-happened_on', '-created_at']
        indexes = [models.Index(fields=['vehicle', 'kind'])]

    def __str__(self):
        return f'{self.get_kind_display()} — {self.vehicle} — {self.happened_on}'

    @property
    def is_outstanding(self):
        """A fine or an accident nobody has closed off yet."""
        return (not self.is_settled
                and self.kind in (self.Kind.FINE, self.Kind.ACCIDENT))


class Place(models.Model):
    """Somewhere people set off from or drive to again and again.

    Every trip so far has started at an AJi office, and every one of those was
    typed out by hand into a search box — which means five spellings of the same
    building and a pin dropped in a slightly different spot each time. A place
    is saved once, with its coordinates, and then it is one click.

    The Bookings desk owns this list. Adding a site that a team visits weekly is
    the same job as adding a car.
    """

    class Kind(models.TextChoices):
        OFFICE = 'office', 'AJi office'
        SITE = 'site', 'Project site'
        OTHER = 'other', 'Somewhere else'

    name = models.CharField(max_length=120,
                            help_text='What people call it. "AJi - Amman Office".')
    address = models.CharField(
        max_length=300, blank=True,
        help_text='The line written onto the booking. Defaults to the name.')
    lat = models.FloatField()
    lng = models.FloatField()
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.OFFICE)

    #: Whose place this is. Blank means everybody sees it, which is what an
    #: office is; a site that only AJiWE ever visits can be narrowed.
    entity = models.ForeignKey(Entity, on_delete=models.SET_NULL, null=True,
                               blank=True, related_name='places')

    #: The one the "starting from" box is filled with before anybody types.
    #: Almost every trip leaves from the office, so making that the default
    #: removes a step from almost every booking.
    is_default_origin = models.BooleanField(
        default=False, verbose_name='Start trips here')

    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name

    @property
    def label(self):
        """What goes on the booking: the address if there is one, else the name."""
        return self.address or self.name

    def save(self, *args, **kwargs):
        # Only one place can be the default origin. Enforced here rather than
        # left to whoever edits the list, because two defaults is not a state
        # the form can do anything sensible with.
        super().save(*args, **kwargs)
        if self.is_default_origin:
            Place.objects.exclude(pk=self.pk).filter(
                is_default_origin=True).update(is_default_origin=False)


class FleetApprover(models.Model):
    """Who signs off a car for one division.

    AJiBE's cars are not AJiWE's to give away, and the two are approved by
    different people, so "the Bookings desk" is not one queue — it is one queue
    per division that happens to be run from the same screen.

    A row per person per division rather than a single approver field, because
    somebody is always on leave, and a division whose only approver is away is a
    division whose cars cannot be booked.

    If a division has no rows at all, the whole Bookings group is asked. That
    fallback matters: the alternative is a request that is submitted, accepted
    by the Hub, and then sits in nobody's queue for ever.
    """
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE,
                               related_name='fleet_approvers')
    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='fleet_approvals')
    #: Tell them by email as well as in the Hub. On for the people who actually
    #: watch a queue; off for a director who is on the list so they can step in.
    notify = models.BooleanField(
        default=True, help_text='Email this person when a request comes in.')
    order = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['entity', 'user'],
                                    name='one_approver_row_per_division'),
        ]
        ordering = ['entity__code', 'order', 'user__first_name']
        verbose_name = 'Fleet approver'

    def __str__(self):
        return f'{self.user.get_full_name() or self.user.username} approves {self.entity.code}'


class FleetReminder(models.Model):
    """One reminder that has already gone out, so it does not go out again.

    The reminder job is meant to be run by a scheduler, and a scheduler will
    run it twice — the machine wakes late, somebody runs it by hand, a retry
    fires. Without a record of what was sent, "insurance expires in 30 days"
    arrives every hour for a month and is then filtered to junk by everybody
    who receives it, which is worse than never sending it at all.

    The row is per car, per thing, per deadline, per stage. A deadline that
    MOVES — the policy is renewed, the service is done — produces a new date
    and so a new row, which is what should happen: that really is a new thing
    to be told about.
    """
    class Kind(models.TextChoices):
        INSURANCE = 'insurance', 'Insurance'
        REGISTRATION = 'registration', 'Licence'
        SERVICE = 'service', 'Service'
        SERVICE_KM = 'service_km', 'Service (kilometres)'

    class Stage(models.TextChoices):
        EARLY = 'early', '30 days'          # the first warning
        SOON = 'soon', '7 days'             # the last one before it lapses
        OVERDUE = 'overdue', 'Overdue'      # it has lapsed

    vehicle = models.ForeignKey(Vehicle, on_delete=models.CASCADE,
                                related_name='reminders')
    kind = models.CharField(max_length=16, choices=Kind.choices)
    stage = models.CharField(max_length=8, choices=Stage.choices)
    #: The deadline this reminder was about. A renewed policy has a new date
    #: and therefore gets its own reminders.
    due_on = models.DateField(null=True, blank=True)
    #: For the kilometre reminder, which has no date.
    due_km = models.PositiveIntegerField(null=True, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)
    recipients = models.CharField(max_length=400, blank=True,
                                  help_text='Who it went to, for the record.')

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['vehicle', 'kind', 'stage', 'due_on', 'due_km'],
                name='one_reminder_per_deadline_per_stage'),
        ]
        ordering = ['-sent_at']
        verbose_name = 'Fleet reminder sent'

    def __str__(self):
        return f'{self.vehicle} — {self.get_kind_display()} ({self.stage})'
