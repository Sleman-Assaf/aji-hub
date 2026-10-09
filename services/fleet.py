# services/fleet.py
"""The rules of the car booking system, kept out of the views.

Three things live here because more than one screen needs each of them and they
must not be allowed to disagree:

  · WHO IS FREE. Asked by the booking form (to offer cars), by the create call
    (to refuse a car taken between the form loading and being sent), and by the
    desk when it reassigns one. Three callers, one answer.

  · THE CALENDAR ENTRY. An approved trip appears on the company calendar, and
    has to keep up when the trip moves or is called off. The booking owns its
    event, so the rule is "make the event match the booking" rather than a
    create here and a delete there.

  · WHO IS TOLD. A request needs the desk to look at it; a decision needs the
    person who asked. Both go through core.notify, so the bell, the desktop
    pop-up and the email all follow the recipient's own preferences rather than
    this module's opinion.
"""
from datetime import datetime, timedelta

from django.contrib.auth.models import Group, User
from django.urls import reverse

from core.models import NotificationEvent
from core.notify import absolute, notify, notify_many, render_email

from .models import (BOOKINGS_GROUP, FleetApprover, Vehicle,
                     VehicleAssignment, VehicleBooking)

#: The old name. Reception still run the desk; the group was renamed rather
#: than replaced, and this is kept so a half-migrated database still works.
LEGACY_GROUP = 'Reception Desk'


# ── who runs the desk ─────────────────────────────────────────────────────
def is_bookings_desk(user):
    """May this person approve bookings and manage the fleet?"""
    if not user.is_authenticated:
        return False
    return (user.is_superuser
            or user.groups.filter(name__in=(BOOKINGS_GROUP, LEGACY_GROUP)).exists())


def desk_users():
    """Everybody who runs the booking system, whatever division."""
    return User.objects.filter(
        groups__name__in=(BOOKINGS_GROUP, LEGACY_GROUP), is_active=True
    ).distinct()


def approvers_for(entity, email_only=False):
    """Who decides on a car belonging to this division.

    Falls back to the whole Bookings group when a division has nobody named.
    That fallback is the important part: without it a request for a division
    somebody forgot to configure would be accepted by the Hub and then sit in
    nobody's queue, which is worse than refusing it.
    """
    rows = FleetApprover.objects.filter(entity=entity, user__is_active=True)
    if email_only:
        rows = rows.filter(notify=True)
    users = [r.user for r in rows.select_related('user')]
    return users or list(desk_users())


def entities_approved_by(user):
    """The divisions this person decides for.

    None means "no restriction": somebody in the Bookings group who is not
    named against any division sees everything, which is how the desk worked
    before divisions had their own approvers and is what a superuser expects.
    """
    if user.is_superuser:
        return None
    codes = list(FleetApprover.objects.filter(user=user)
                 .values_list('entity_id', flat=True))
    return codes or None


def can_review(user, booking):
    """May this person decide on this particular booking?"""
    if not is_bookings_desk(user):
        return False
    allowed = entities_approved_by(user)
    return allowed is None or booking.entity_id in allowed


# ── when ──────────────────────────────────────────────────────────────────
def window(date, departure_time, duration_minutes):
    """The datetime span a trip occupies.

    A span, not two clock times: a car leaving at ten at night for four hours
    comes back tomorrow, and comparing bare times would decide that trip ends
    before it begins and clashes with nothing.
    """
    start = datetime.combine(date, departure_time)
    return start, start + timedelta(minutes=duration_minutes or 0)


def _live_bookings_near(date):
    """Bookings that could possibly touch this date.

    The day before is included because a trip can run past midnight into it;
    the day after for the same reason in the other direction. Cheaper than
    scanning the table and correct in a way that filtering on `date` alone is
    not.
    """
    return (VehicleBooking.objects
            .filter(status__in=VehicleBooking.HOLDS_CAR,
                    date__range=(date - timedelta(days=1), date + timedelta(days=1)))
            .prefetch_related('assignments__vehicle'))


def clashes(start, end, entity=None, exclude_pk=None):
    """Which cars are busy in this window, and what is using them.

    Returns {vehicle_id: [booking, …]}. A car with no entry is free.
    """
    busy = {}
    for b in _live_bookings_near(start.date()):
        if b.pk == exclude_pk:
            continue
        if not (b.start < end and start < b.end):
            continue
        for a in b.assignments.all():
            if entity and a.vehicle.entity_id != entity.pk:
                continue
            busy.setdefault(a.vehicle_id, []).append(b)
    return busy


def availability(entity, start, end, exclude_pk=None):
    """Every car of this division, each marked free or not, and why not.

    Cars that are out of service are included rather than hidden: somebody
    looking for a car needs to know the third one exists and is in the garage,
    or they will ask why it is missing.
    """
    busy = clashes(start, end, entity=entity, exclude_pk=exclude_pk)
    rows = []
    for v in Vehicle.objects.filter(entity=entity).exclude(
            status=Vehicle.Status.RETIRED).select_related('entity'):
        taken = busy.get(v.pk, [])
        if v.status != Vehicle.Status.ACTIVE:
            reason = v.get_status_display()
        elif taken:
            b = taken[0]
            reason = (f'Booked {b.departure_time:%H:%M}–{b.return_time:%H:%M}'
                      f' by {b.booked_by.get_full_name() or b.booked_by.username}')
        else:
            reason = ''
        rows.append({'vehicle': v, 'free': not taken and v.is_bookable,
                     'reason': reason, 'alerts': v.alerts})
    return rows


def free_vehicle_ids(entity, start, end, exclude_pk=None):
    return {r['vehicle'].pk for r in availability(entity, start, end, exclude_pk) if r['free']}


# ── the calendar ──────────────────────────────────────────────────────────
def sync_calendar_event(booking):
    """Make the company calendar agree with this booking.

    Called after every change, including the ones that should remove the entry,
    so there is a single rule — the event matches the booking — instead of a
    create in one view and a delete in another that will eventually disagree.
    """
    from content.models import CalendarEvent

    should_exist = booking.status == VehicleBooking.Status.APPROVED
    event = booking.calendar_event

    if not should_exist:
        if event:
            booking.calendar_event = None
            booking.save(update_fields=['calendar_event'])
            event.delete()
        return None

    who = booking.booked_by.get_full_name() or booking.booked_by.username
    fields = dict(
        title=f'Car · {booking.to_address[:60]} · {who}',
        date=booking.date,
        end_date=booking.end.date() if booking.end.date() != booking.date else None,
        time=booking.departure_time,
        end_time=booking.return_time,
        location=f'{booking.from_address} → {booking.to_address}'[:300],
        event_type=CalendarEvent.EventType.OTHER,
        all_day=False,
        description=(f'{booking.cars_label}\n'
                     f'{booking.attendees} travelling'
                     + (' · driver requested' if booking.driver_required else '')
                     + (f'\n{booking.purpose}' if booking.purpose else '')),
        # The whole company does not need every car trip on its month view.
        is_public=False,
        created_by=booking.booked_by,
    )
    if event:
        for k, v in fields.items():
            setattr(event, k, v)
        event.save()
    else:
        event = CalendarEvent.objects.create(**fields)
        booking.calendar_event = event
        booking.save(update_fields=['calendar_event'])
    return event


# ── who is told ───────────────────────────────────────────────────────────
def _summary(booking):
    """One line, for the bell — where thirty other rows compete for attention."""
    return (f'{booking.date:%a %d %b} at {booking.departure_time:%H:%M} · '
            f'{booking.from_address[:40]} to {booking.to_address[:40]} · '
            f'{booking.duration_label}')


def details(booking):
    """The whole booking as label/value pairs, for the emails.

    An approver deciding on a car should not have to open the Hub to find out
    where it is going, who is in it, or whether a driver was asked for. The
    same table goes to the person who asked, so what they were told matches
    what was decided on, field for field.
    """
    who = booking.booked_by.get_full_name() or booking.booked_by.username
    back = (f'{booking.return_date:%A %d %B} at {booking.return_time:%H:%M}'
            if booking.is_multi_day
            else f'{booking.return_time:%H:%M}, the same day')
    rows = [
        ('Requested by', who),
        ('Division', booking.entity.code),
        ('Leaving', f'{booking.date:%A %d %B %Y} at {booking.departure_time:%H:%M}'),
        ('Back', f'{back}   ({booking.duration_label})'),
        ('From', booking.from_address),
        ('To', booking.to_address),
        ('Travelling', f'{booking.attendees} '
                       + ('person' if booking.attendees == 1 else 'people')),
        ('Driver', 'Requested' if booking.driver_required else 'Not needed'),
        ('Purpose', booking.purpose),
        ('Notes', booking.notes),
    ]
    cars = booking.cars
    if cars:
        rows.append(('Car' if len(cars) == 1 else 'Cars',
                     ', '.join((f'{c.make} {c.model}'.strip()
                                + (f' ({c.plate})' if c.plate else ''))
                               for c in cars)))
    driven = [a for a in booking.assignments.all() if a.driver_id]
    if driven:
        rows.append(('Assigned driver' + ('' if len(driven) == 1 else 's'),
                     ', '.join(f'{a.driver.full_name} on the {a.vehicle.make}'
                               for a in driven)))
    if booking.review_comment:
        rows.append(('Note from the desk', booking.review_comment))
    return rows


def notify_submitted(booking):
    """Tell the approvers it is waiting, and tell the asker it arrived.

    Approvers are per division. AJiBE's cars are approved by different people
    from AJiWE's, and sending every request to everybody is how a queue stops
    being read at all.

    Two rules here are deliberate departures from how the rest of the Hub
    notifies.

    First, no `actor` is passed, so the suppression that normally stops anybody
    hearing about their own action does not apply. It is right for a comment or
    a like; it is wrong for a car. An approver who books a car still has a
    request sitting in a queue, and the email is the record that it is there.

    Second, whoever asked is told as well, and not only when a decision is
    made. A request that produces silence for a day feels like a request that
    went nowhere, and the sender has no copy of what they asked for.

    One person gets one email. An approver who books their own trip gets the
    approval email — it says everything the confirmation says and carries the
    button as well — reworded so it does not report them to themselves.
    """
    who = booking.booked_by.get_full_name() or booking.booked_by.username
    rows = details(booking)
    summary = _summary(booking)
    desk_link = reverse('services:car_request_detail', args=[booking.pk])
    tally = {'in_app': 0, 'email': 0}

    approvers = approvers_for(booking.entity)
    # Named approvers who asked not to be emailed get the bell only. The flag
    # exists for the director who is on the list so they can step in.
    quiet = set(FleetApprover.objects
                .filter(entity=booking.entity, notify=False)
                .values_list('user_id', flat=True))
    for user in approvers:
        mine = user.pk == booking.booked_by_id
        lead = ('You have asked for a car. It is in the queue — you can '
                'approve it yourself, or leave it to one of the other people '
                f'who approve for {booking.entity.code}.') if mine else (
            f'{who} has asked for a car. It is waiting on you.')
        html = render_email(
            title=f'Car booking request - {booking.entity.code}',
            lines=[lead], rows=rows,
            link=absolute(desk_link), link_label='Open the approval page',
            footer='You are getting this because you approve car bookings for '
                   f'{booking.entity.code}.')
        out = notify(
            user, NotificationEvent.APPROVAL,
            title=('Your car request is in the queue (%s)' % booking.entity.code
                   if mine else f'Car request from {who} ({booking.entity.code})'),
            body=summary, link=desk_link,
            email_subject=(f'Car booking request - {booking.entity.code} - '
                           f'{booking.date:%d %b} - {who}'),
            email_html=html,
            email=user.pk not in quiet)
        tally['in_app'] += bool(out['in_app'])
        tally['email'] += bool(out['email'])

    if booking.booked_by_id not in {u.pk for u in approvers}:
        names = ', '.join((u.get_full_name() or u.username) for u in approvers)
        mine_link = reverse('services:car_booking') + f'?booking={booking.pk}'
        html = render_email(
            title='Car booking request sent',
            lines=['Your request is with the Bookings desk. Here it is in full '
                   '— you will get another email as soon as it is decided.']
                  + ([f'Waiting on {names}.'] if names else []),
            rows=rows,
            link=absolute(mine_link), link_label='See it in the Hub',
            footer='Nothing is held for you until it is approved.')
        out = notify(
            booking.booked_by, NotificationEvent.APPROVAL,
            title='Your car request was sent to the Bookings desk',
            body=summary, link=mine_link,
            email_subject=(f'Car booking request sent - {booking.date:%d %b} - '
                           f'{booking.to_address[:40]}'),
            email_html=html)
        tally['in_app'] += bool(out['in_app'])
        tally['email'] += bool(out['email'])

    return tally


def notify_decided(booking, swapped=False):
    """Tell the person who asked what was decided, in full.

    `swapped` is for the case the desk changed the car on a trip that was
    already settled: the same email, but it says so, because somebody who has
    been told they have the Outlander will go and look for the Outlander.
    """
    verb = {VehicleBooking.Status.APPROVED: 'approved',
            VehicleBooking.Status.REJECTED: 'declined',
            VehicleBooking.Status.CANCELLED: 'cancelled'}.get(booking.status,
                                                              'updated')
    lead = {
        'approved': 'Your car booking is confirmed. Here it is in full.',
        'declined': 'Your car booking was not approved.',
        'cancelled': 'Your car booking has been cancelled.',
    }.get(verb, 'Your car booking has changed.')
    if swapped:
        verb = 'changed'
        lead = ('The Bookings desk has changed the car on your trip. '
                'Here it is again in full.')
    by = ''
    if booking.reviewed_by_id:
        by = (booking.reviewed_by.get_full_name() or booking.reviewed_by.username)
    link = reverse('services:car_booking') + f'?booking={booking.pk}'
    html = render_email(
        title=f'Car booking {verb}',
        lines=[lead] + ([f'Decided by {by}.'] if by else []),
        rows=details(booking),
        link=absolute(link), link_label='See it in the Hub',
        footer=('Keys are collected from the Bookings desk.'
                if booking.status == VehicleBooking.Status.APPROVED else ''))
    body = _summary(booking)
    if booking.status == VehicleBooking.Status.APPROVED:
        body += '\n' + booking.cars_label
    if booking.review_comment:
        body += '\n' + booking.review_comment
    return notify_many(
        [booking.booked_by], NotificationEvent.APPROVAL,
        title=f'Your car booking was {verb}',
        body=body, link=link,
        email_subject=(f'Car booking {verb} - {booking.date:%d %b} - '
                       f'{booking.to_address[:40]}'),
        email_html=html,
        # No actor, on purpose: an approver who approves their own trip still
        # wants the confirmation, because the confirmation is the thing with
        # the car, the times and the desk's note on it.
    )


# ══ what is about to run out ══════════════════════════════════════════════
#  Insurance lapses on a date, the licence lapses on a date, a service is due
#  on a date or at a reading. All four are the same question — "how close is
#  it?" — asked of different fields, so they are answered in one place and
#  emailed in one email per division rather than four per car.

#: How close counts as worth an email, per stage.
REMIND_EARLY = 30       # days
REMIND_SOON = 7         # days
REMIND_KM = 500         # kilometres left on a service interval


def _stage_for_days(days):
    """Which warning a deadline this far off deserves, or None."""
    from .models import FleetReminder
    if days < 0:
        return FleetReminder.Stage.OVERDUE
    if days <= REMIND_SOON:
        return FleetReminder.Stage.SOON
    if days <= REMIND_EARLY:
        return FleetReminder.Stage.EARLY
    return None


def due_items(vehicle, today=None):
    """Everything on this car that is close to running out.

    Returns dicts, one per thing: kind, stage, the deadline, and a line of
    English. Read by the reminder job and by the fleet screens, so the email
    and the page can never disagree about what is wrong with a car.
    """
    from datetime import date
    from .models import FleetReminder

    today = today or date.today()
    out = []

    for kind, when, what in (
            (FleetReminder.Kind.INSURANCE, vehicle.insurance_expiry, 'Insurance'),
            (FleetReminder.Kind.REGISTRATION, vehicle.registration_expiry, 'Licence'),
            (FleetReminder.Kind.SERVICE, vehicle.next_service_due, 'Service')):
        if not when:
            continue
        days = (when - today).days
        stage = _stage_for_days(days)
        if not stage:
            continue
        out.append({
            'vehicle': vehicle,
            'kind': kind, 'stage': stage, 'due_on': when, 'due_km': None,
            'what': what, 'days': days,
            'text': (f'{what} expired {abs(days)} days ago, on {when:%d %b %Y}'
                     if days < 0 else
                     f'{what} runs out in {days} day{"" if days == 1 else "s"}, '
                     f'on {when:%d %b %Y}'),
        })

    # The service interval in kilometres, which has no date at all: it comes
    # due when the odometer gets there, and the odometer only moves when a
    # trip is logged.
    if vehicle.next_service_km:
        left = vehicle.next_service_km - (vehicle.odometer or 0)
        if left <= REMIND_KM:
            stage = (FleetReminder.Stage.OVERDUE if left < 0
                     else FleetReminder.Stage.SOON if left <= REMIND_KM / 2
                     else FleetReminder.Stage.EARLY)
            out.append({
                'vehicle': vehicle,
                'kind': FleetReminder.Kind.SERVICE_KM, 'stage': stage,
                'due_on': None, 'due_km': vehicle.next_service_km,
                'what': 'Service', 'days': None,
                'text': (f'Service was due {abs(left):,} km ago '
                         f'(at {vehicle.next_service_km:,} km, now on '
                         f'{vehicle.odometer or 0:,})' if left < 0 else
                         f'Service due in {left:,} km '
                         f'(at {vehicle.next_service_km:,} km)'),
            })

    order = {FleetReminder.Stage.OVERDUE: 0, FleetReminder.Stage.SOON: 1,
             FleetReminder.Stage.EARLY: 2}
    return sorted(out, key=lambda i: order[i['stage']])


def notify_due(entity, items, dry_run=False):
    """One email to one division's approvers about all of its cars.

    Per division rather than per car because five cars needing attention is
    one conversation with one person, and five emails about it is the thing
    that makes somebody stop reading the first one.
    """
    from .models import FleetReminder

    people = approvers_for(entity)
    if not people:
        return {'in_app': 0, 'email': 0, 'to': []}

    quiet = set(FleetApprover.objects.filter(entity=entity, notify=False)
                .values_list('user_id', flat=True))
    worst = min(items, key=lambda i: 0 if i['stage'] == FleetReminder.Stage.OVERDUE
                else 1 if i['stage'] == FleetReminder.Stage.SOON else 2)
    overdue = [i for i in items if i['stage'] == FleetReminder.Stage.OVERDUE]

    def name(v):
        make = f'{v.make} {v.model}'.strip()
        return f'{make} ({v.plate})' if v.plate else make

    rows = [(name(i['vehicle']), i['text']) for i in items]
    lead = ('These %s cars need attention before they can be booked.'
            % entity.code if len(items) > 1 else
            'A %s car needs attention.' % entity.code)
    link = reverse('services:fleet_console')
    html = render_email(
        title=f'{entity.code} fleet - {len(items)} thing'
              f'{"" if len(items) == 1 else "s"} to renew',
        lines=[lead] + (['%d of them have already lapsed. A car with expired '
                         'insurance should not be on the road.' % len(overdue)]
                        if overdue else []),
        rows=rows,
        link=absolute(link), link_label='Open the fleet',
        footer='You are getting this because you approve car bookings for '
               f'{entity.code}.')
    subject = (f'{entity.code} fleet - '
               + ('%d overdue' % len(overdue) if overdue
                  else 'renewals due'))
    body = ' · '.join(f"{i['vehicle'].plate or i['vehicle'].make}: {i['text']}"
                      for i in items[:4])

    if dry_run:
        return {'in_app': 0, 'email': 0,
                'to': [u.email for u in people if u.pk not in quiet]}

    tally = {'in_app': 0, 'email': 0, 'to': []}
    for user in people:
        # Its own event, not APPROVAL: wanting every car request in your
        # inbox and wanting a renewal notice a month ahead are two different
        # appetites, and one switch cannot hold both.
        out = notify(user, NotificationEvent.RENEWAL,
                     title=f'{entity.code} fleet: {worst["what"].lower()} to renew',
                     body=body, link=link,
                     email_subject=subject, email_html=html,
                     email=user.pk not in quiet)
        tally['in_app'] += bool(out['in_app'])
        tally['email'] += bool(out['email'])
        if out['email']:
            tally['to'].append(user.email)
    return tally
