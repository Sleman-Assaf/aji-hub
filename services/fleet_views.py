# services/fleet_views.py
"""Car booking: the request side for everybody, the desk side for the Bookings
group, and one small proxy so the map can search for an address.

Kept apart from views.py, which is already a thousand lines of a different
subject. Everything that decides anything lives in fleet.py; this module reads
requests, calls that, and renders.
"""
import base64
import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import (Http404, HttpResponse, HttpResponseBadRequest,
                         JsonResponse)
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from people.models import Employee, Entity

from . import fleet
from .models import (FleetApprover, Place, Vehicle, VehicleAssignment,
                     VehicleBooking, VehicleEvent, VehicleTripLog)

#: The longest a car can be signed out for in one booking. A fortnight is
#: generous for a site trip and still catches the typo that asks for a year.
MAX_MINUTES = 14 * 24 * 60


def _user_entity(user):
    """The division this person belongs to, as a sensible default."""
    emp = Employee.objects.filter(user=user).select_related('entity').first()
    return emp.entity if emp and emp.entity_id else None


def _entities_with_cars():
    """Only divisions that actually own a car. Offering AJi Saudi on a form
    that will then show 'no cars' is a dead end the user cannot act on."""
    return (Entity.objects
            .filter(vehicles__isnull=False)
            .exclude(vehicles__status=Vehicle.Status.RETIRED)
            .distinct().order_by('code'))


def _map_context():
    """The tile source, so a template never hardcodes one.

    Read from settings rather than written into the page, because which tile
    server AJi is allowed to hit is a deployment question — OpenStreetMap's own
    volunteer servers block a corporate intranet, and the answer may later be a
    key or a box inside the network.
    """
    from django.urls import reverse
    tiles = settings.MAP_TILE_URL or (
        reverse('services:map_tile', args=[0, 0, 0])
        .replace('/0/0/0.png', '/{z}/{x}/{y}.png'))
    return {
        'map_tiles': tiles,
        'map_attribution': settings.MAP_TILE_ATTRIBUTION,
        'map_subdomains': settings.MAP_TILE_SUBDOMAINS,
        'map_home': list(settings.MAP_HOME),
    }


def _parse_when(request, data):
    """(date, time, minutes) out of a request, or a ValueError with a reason.

    The form asks when the car goes out and when it comes back, which is what
    anybody actually knows — "out for about four hours" was a guess dressed up
    as a fact. The minutes between the two are what gets stored, because every
    availability question is a question about a span.
    """
    def day(key, what):
        try:
            return datetime.strptime(data.get(key, ''), '%Y-%m-%d').date()
        except ValueError:
            raise ValueError(what)

    def clock(key, what):
        try:
            return datetime.strptime(data.get(key, ''), '%H:%M').time()
        except ValueError:
            raise ValueError(what)

    d = day('date', 'Pick the day you are leaving.')
    t = clock('departure_time', 'Pick the time you are leaving.')

    # A return date if one was sent, otherwise the same day. Keyed on the field
    # being present rather than on the multi-day tick box, so the desk's own
    # screen — which has no tick box — can post the same shape.
    end_d = (day('return_date', 'Pick the day you are coming back.')
             if data.get('return_date') else d)
    end_t = clock('return_time', 'Pick the time you are coming back.')

    mins = int((datetime.combine(end_d, end_t)
                - datetime.combine(d, t)).total_seconds() // 60)
    if mins <= 0:
        raise ValueError('The car has to come back after it leaves.')
    if mins > MAX_MINUTES:
        raise ValueError('That is longer than two weeks. Split it into two bookings.')
    return d, t, mins


# ══════════════════════════════════════════════════════════════════════════
#  ASKING FOR A CAR
# ══════════════════════════════════════════════════════════════════════════
@login_required
def car_booking(request):
    """The request page, and this person's own bookings under it."""
    mine = (VehicleBooking.objects
            .filter(booked_by=request.user)
            .select_related('entity', 'reviewed_by')
            .prefetch_related('assignments__vehicle', 'assignments__driver')
            .order_by('-date', '-departure_time')[:40])

    default_entity = _user_entity(request.user)
    entities = list(_entities_with_cars())
    if default_entity and default_entity not in entities:
        # They belong to a division with no cars. Do not preselect a division
        # whose fleet is empty — let them choose one on purpose.
        default_entity = None

    return render(request, 'services/car_booking.html', {
        'entities': entities,
        'default_entity': default_entity,
        'my_bookings': mine,
        'today': date.today().isoformat(),
        'is_desk': fleet.is_bookings_desk(request.user),
        'pending_count': (VehicleBooking.objects.filter(
            status=VehicleBooking.Status.PENDING).count()
            if fleet.is_bookings_desk(request.user) else 0),
        'focus_booking': request.GET.get('booking', ''),
        'places': Place.objects.filter(is_active=True),
        'default_origin': Place.objects.filter(is_active=True,
                                               is_default_origin=True).first(),
        **_map_context(),
    })


def _parse_span(data):
    """(date, time, minutes) for the availability call.

    Separate from _parse_when because the two speak different languages and
    should not be made to share one. A PERSON says "leaving Tuesday at nine,
    back Thursday at four"; the page has already turned that into a number of
    minutes before it asks what is free. Forcing the API to re-read the form's
    fields was how it ended up answering "pick the time you are coming back"
    to a question that had nothing to do with a form.
    """
    try:
        d = datetime.strptime(data.get('date', ''), '%Y-%m-%d').date()
        t = datetime.strptime(data.get('departure_time', ''), '%H:%M').time()
        mins = int(data.get('duration_minutes', 0))
    except (TypeError, ValueError):
        raise ValueError('Set the dates and times first.')
    if not 0 < mins <= MAX_MINUTES:
        raise ValueError('Set the dates and times first.')
    return d, t, mins


@login_required
def car_availability_api(request):
    """Which cars of a division are free in a given window."""
    try:
        entity = Entity.objects.get(pk=request.GET.get('entity'))
    except (Entity.DoesNotExist, ValueError, TypeError):
        return JsonResponse({'error': 'Choose a division.'}, status=400)
    try:
        d, t, mins = _parse_span(request.GET)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)

    start, end = fleet.window(d, t, mins)
    exclude = request.GET.get('exclude')
    rows = fleet.availability(entity, start, end,
                              exclude_pk=int(exclude) if exclude else None)
    return JsonResponse({
        'window': {'start': start.strftime('%H:%M'),
                   'end': end.strftime('%H:%M'),
                   # The day it comes back, in words, or empty when that is
                   # the same day. "the next day" was wrong for anything that
                   # runs longer than one night.
                   'end_day': ('' if end.date() == start.date()
                               else end.strftime('%a %d %b'))},
        'cars': [{
            'id': r['vehicle'].pk,
            'label': r['vehicle'].label,
            'make': r['vehicle'].make,
            'model': r['vehicle'].model,
            'plate': r['vehicle'].plate,
            'seats': r['vehicle'].seats,
            'photo': r['vehicle'].photo.url if r['vehicle'].photo else '',
            'free': r['free'],
            'reason': r['reason'],
            'alerts': [a[1] for a in r['alerts']],
        } for r in rows],
    })


@login_required
@require_POST
def car_booking_create(request):
    """Take a request. Re-checks availability rather than trusting the form.

    The form showed what was free when it loaded. Between then and now somebody
    else may have taken the same car, so the check is done again here, inside
    the transaction that writes the assignment.
    """
    data = request.POST
    try:
        entity = Entity.objects.get(pk=data.get('entity'))
    except (Entity.DoesNotExist, ValueError, TypeError):
        return JsonResponse({'error': 'Choose a division.'}, status=400)
    try:
        d, t, mins = _parse_when(request, data)
    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)

    from_address = (data.get('from_address') or '').strip()
    to_address = (data.get('to_address') or '').strip()
    if not from_address or not to_address:
        return JsonResponse({'error': 'Both the start and the destination are needed.'},
                            status=400)

    # Why the car is wanted is not optional. When two people want the only
    # Sportage on Tuesday morning the desk has to choose between them, and
    # "site visit in Zarqa" against "meeting, might get a taxi" is the whole
    # basis for that choice.
    purpose = (data.get('purpose') or '').strip()[:300]
    if not purpose:
        return JsonResponse(
            {'error': 'Say what the trip is for — a site visit, a meeting, '
                      'a delivery.'}, status=400)

    # One trip, one car. The desk can still put a second car on a booking when
    # it reviews it — a party of nine is their problem to solve, and they know
    # what else is out that morning — but the person asking chooses one.
    car_ids = [int(x) for x in data.getlist('cars') if str(x).isdigit()][:1]
    if not car_ids:
        return JsonResponse({'error': 'Choose a car.'}, status=400)

    try:
        attendees = max(1, int(data.get('attendees') or 1))
    except ValueError:
        attendees = 1

    start, end = fleet.window(d, t, mins)

    def _f(name):
        v = data.get(name)
        try:
            return float(v) if v not in (None, '') else None
        except ValueError:
            return None

    with transaction.atomic():
        free = fleet.free_vehicle_ids(entity, start, end)
        taken = [c for c in car_ids if c not in free]
        if taken:
            names = ', '.join(Vehicle.objects.filter(pk__in=taken)
                              .values_list('make', flat=True))
            return JsonResponse(
                {'error': f'Taken while you were filling this in: {names}. '
                          f'Pick again.'}, status=409)

        booking = VehicleBooking.objects.create(
            booked_by=request.user, entity=entity, date=d,
            departure_time=t, duration_minutes=mins,
            from_address=from_address, from_lat=_f('from_lat'), from_lng=_f('from_lng'),
            to_address=to_address, to_lat=_f('to_lat'), to_lng=_f('to_lng'),
            driver_required=data.get('driver_required') in ('1', 'true', 'on'),
            attendees=attendees,
            purpose=purpose,
            notes=(data.get('notes') or '').strip(),
        )
        VehicleAssignment.objects.bulk_create(
            [VehicleAssignment(booking=booking, vehicle_id=c) for c in car_ids])

    fleet.notify_submitted(booking)
    return JsonResponse({'ok': True, 'id': booking.pk,
                         'message': 'Sent to the Bookings desk.'})


@login_required
@require_POST
def car_booking_cancel(request, pk):
    """Called off by the person who asked, or by the desk."""
    booking = get_object_or_404(VehicleBooking, pk=pk)
    if booking.booked_by_id != request.user.pk and not fleet.is_bookings_desk(request.user):
        return JsonResponse({'error': 'Not yours to cancel.'}, status=403)
    if booking.status == VehicleBooking.Status.CANCELLED:
        return JsonResponse({'ok': True})

    booking.status = VehicleBooking.Status.CANCELLED
    booking.review_comment = (request.POST.get('reason') or '')[:300]
    booking.save(update_fields=['status', 'review_comment', 'updated_at'])
    fleet.sync_calendar_event(booking)
    if booking.booked_by_id != request.user.pk:
        fleet.notify_decided(booking)
    return JsonResponse({'ok': True})


# ══════════════════════════════════════════════════════════════════════════
#  THE MAP
# ══════════════════════════════════════════════════════════════════════════
@login_required
def geocode_api(request):
    """Address search, proxied.

    Through the server rather than from the browser for three reasons: the
    search provider asks for a contact in the User-Agent and a page cannot set
    one; the answers are cached here so twenty people typing "Abdali" cost one
    request; and the browser never talks to a third party directly, so no
    employee's address bar leaves the network.
    """
    q = (request.GET.get('q') or '').strip()
    if len(q) < 3:
        return JsonResponse({'results': []})

    key = 'geo:' + q.lower()
    hit = cache.get(key)
    if hit is not None:
        return JsonResponse({'results': hit, 'cached': True})

    url = ('https://nominatim.openstreetmap.org/search?'
           + urllib.parse.urlencode({'q': q, 'format': 'json', 'limit': 6,
                                     'addressdetails': 1}))
    req = urllib.request.Request(url, headers={
        'User-Agent': 'AJi Hub intranet (intranet@ajigroup.com)',
        'Accept-Language': 'en',
    })
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            raw = json.loads(r.read().decode('utf-8'))
    except Exception:
        # A search that cannot reach the internet is not an error the user can
        # do anything about — they can still drop a pin or type the address.
        return JsonResponse({'results': [], 'offline': True})

    results = [{'label': x.get('display_name', ''),
                'lat': float(x['lat']), 'lng': float(x['lon'])}
               for x in raw if x.get('lat') and x.get('lon')]
    cache.set(key, results, 60 * 60 * 24)
    return JsonResponse({'results': results})


# ══════════════════════════════════════════════════════════════════════════
#  THE DESK
#
#  Everything below is for the Bookings group. The gate is one decorator, so
#  there is no view where somebody forgot to check.
# ══════════════════════════════════════════════════════════════════════════
def desk_only(view):
    from functools import wraps

    @wraps(view)
    @login_required
    def wrapper(request, *args, **kwargs):
        if not fleet.is_bookings_desk(request.user):
            raise Http404
        return view(request, *args, **kwargs)
    return wrapper


def _scoped(user):
    """Bookings this person is allowed to see, before any other filter."""
    qs = VehicleBooking.objects.all()
    mine = fleet.entities_approved_by(user)
    return qs if mine is None else qs.filter(entity_id__in=mine)


@desk_only
def car_requests(request):
    """The inbox: what is waiting, and what has been decided."""
    show = request.GET.get('show', 'pending')
    qs = (VehicleBooking.objects
          .select_related('entity', 'booked_by', 'reviewed_by')
          .prefetch_related('assignments__vehicle', 'assignments__driver'))
    # Somebody who approves AJiBE should not be looking at AJiWE's queue, and
    # more to the point should not be able to decide on it. None means they
    # are not tied to a division and see the lot.
    mine = fleet.entities_approved_by(request.user)
    if mine is not None:
        qs = qs.filter(entity_id__in=mine)
    if show == 'pending':
        qs = qs.filter(status=VehicleBooking.Status.PENDING)
    elif show == 'upcoming':
        qs = qs.filter(status=VehicleBooking.Status.APPROVED, date__gte=date.today())
    elif show == 'past':
        qs = qs.filter(date__lt=date.today())
    q = (request.GET.get('q') or '').strip()
    if q:
        qs = qs.filter(Q(to_address__icontains=q) | Q(from_address__icontains=q)
                       | Q(purpose__icontains=q)
                       | Q(booked_by__first_name__icontains=q)
                       | Q(booked_by__last_name__icontains=q))

    return render(request, 'services/car_requests.html', {
        'bookings': qs.order_by('-date', '-departure_time')[:200],
        'show': show, 'q': q,
        'counts': {
            'pending': _scoped(request.user).filter(
                status=VehicleBooking.Status.PENDING).count(),
            'upcoming': _scoped(request.user).filter(
                status=VehicleBooking.Status.APPROVED, date__gte=date.today()).count(),
        },
        'divisions': fleet.entities_approved_by(request.user),
    })


@desk_only
def car_request_detail(request, pk):
    """One request, with the cars that are actually free for it."""
    booking = get_object_or_404(
        VehicleBooking.objects.select_related('entity', 'booked_by', 'reviewed_by')
        .prefetch_related('assignments__vehicle', 'assignments__driver'), pk=pk)
    if not fleet.can_review(request.user, booking):
        raise Http404
    rows = fleet.availability(booking.entity, booking.start, booking.end,
                              exclude_pk=booking.pk)
    chosen = {a.vehicle_id for a in booking.assignments.all()}
    for r in rows:
        r['chosen'] = r['vehicle'].pk in chosen

    # One odometer panel per car on the trip, carrying whatever has been
    # recorded so far. The times are pre-filled from the booking so the desk
    # confirms a time rather than typing one.
    logs = {l.vehicle_id: l for l in booking.trip_logs.select_related('driver')}
    trips = []
    for a in booking.assignments.select_related('vehicle', 'driver'):
        log = logs.get(a.vehicle_id) or VehicleTripLog(
            booking=booking, vehicle=a.vehicle, driver=a.driver)
        trips.append({
            'vehicle': a.vehicle, 'log': log,
            'left_value': (log.left_at or booking.start).strftime('%Y-%m-%dT%H:%M'),
            'back_value': (log.returned_at.strftime('%Y-%m-%dT%H:%M')
                           if log.returned_at else
                           booking.end.strftime('%Y-%m-%dT%H:%M')),
        })

    return render(request, 'services/car_request_detail.html', {
        'b': booking, 'rows': rows, 'trips': trips,
        'drivers': Employee.objects.filter(is_active=True).order_by('full_name'),
        **_map_context(),
    })


@desk_only
@require_POST
def car_request_review(request, pk):
    """Approve, decline, or change the trip and then approve it.

    The desk may move the time, swap the cars and name a driver in one action,
    because that is what actually happens: the answer to "can I have a car at
    ten" is usually "you can have a different one, at eleven".
    """
    booking = get_object_or_404(VehicleBooking, pk=pk)
    if not fleet.can_review(request.user, booking):
        raise Http404
    action = request.POST.get('action')
    if action not in ('approve', 'reject', 'save'):
        return redirect('services:car_request_detail', pk=pk)

    if request.POST.get('date'):
        try:
            d, t, mins = _parse_when(request, request.POST)
            booking.date, booking.departure_time, booking.duration_minutes = d, t, mins
        except ValueError as e:
            messages.error(request, str(e))
            return redirect('services:car_request_detail', pk=pk)

    was_approved = booking.status == VehicleBooking.Status.APPROVED
    cars_before = set(booking.assignments.values_list('vehicle_id', flat=True))
    car_ids = [int(x) for x in request.POST.getlist('cars') if str(x).isdigit()]
    if car_ids:
        free = fleet.free_vehicle_ids(booking.entity, booking.start, booking.end,
                                      exclude_pk=booking.pk)
        clash = [c for c in car_ids if c not in free]
        if clash:
            names = ', '.join(Vehicle.objects.filter(pk__in=clash)
                              .values_list('make', flat=True))
            messages.error(request, 'Not free at that time: ' + names)
            return redirect('services:car_request_detail', pk=pk)
        with transaction.atomic():
            booking.assignments.exclude(vehicle_id__in=car_ids).delete()
            have = set(booking.assignments.values_list('vehicle_id', flat=True))
            VehicleAssignment.objects.bulk_create(
                [VehicleAssignment(booking=booking, vehicle_id=c)
                 for c in car_ids if c not in have])

    # a driver per car, where one was asked for
    for a in booking.assignments.select_related('vehicle'):
        key = 'driver_%d' % a.vehicle_id
        if key in request.POST:
            val = request.POST.get(key)
            a.driver_id = int(val) if val else None
            a.save(update_fields=['driver'])

    booking.review_comment = (request.POST.get('review_comment') or '')[:300]
    if action == 'approve':
        booking.status = VehicleBooking.Status.APPROVED
    elif action == 'reject':
        booking.status = VehicleBooking.Status.REJECTED
    booking.reviewed_by = request.user
    booking.reviewed_at = timezone.now()
    booking.save()

    fleet.sync_calendar_event(booking)
    if action in ('approve', 'reject'):
        fleet.notify_decided(booking)
        messages.success(request, 'Booking ' + booking.get_status_display().lower() + '.')
    else:
        # A swap on a trip that was already confirmed has to reach the person
        # who is going to walk out and look for the car they were promised.
        swapped = (was_approved and car_ids
                   and set(car_ids) != cars_before)
        if swapped:
            fleet.notify_decided(booking, swapped=True)
        messages.success(request, 'Car changed — they have been told.'
                         if swapped else 'Saved.')
    return redirect('services:car_requests')


@desk_only
def car_schedule(request):
    """Who has what, day by day, across the whole fleet."""
    try:
        start = datetime.strptime(request.GET.get('from', ''), '%Y-%m-%d').date()
    except ValueError:
        start = date.today()
    days = [start + timedelta(days=i) for i in range(7)]

    cars = list(Vehicle.objects.exclude(status=Vehicle.Status.RETIRED)
                .select_related('entity'))
    live = (VehicleBooking.objects
            .filter(status__in=VehicleBooking.HOLDS_CAR,
                    date__range=(start - timedelta(days=1), start + timedelta(days=7)))
            .select_related('booked_by')
            .prefetch_related('assignments__vehicle'))

    # {(vehicle_id, day): [booking, ...]} — the template cannot build a
    # composite key, so the rows are flattened onto each car here instead.
    grid = {}
    for b in live:
        for a in b.assignments.all():
            for day in days:
                if b.start.date() <= day <= b.end.date():
                    grid.setdefault((a.vehicle_id, day), []).append(b)
    rows = [{'car': c, 'cells': [{'day': d, 'bookings': grid.get((c.pk, d), [])}
                                 for d in days]} for c in cars]

    return render(request, 'services/car_schedule.html', {
        'rows': rows, 'days': days,
        'prev': (start - timedelta(days=7)).isoformat(),
        'next': (start + timedelta(days=7)).isoformat(),
        'today': date.today(),
    })


# ── the fleet itself ──────────────────────────────────────────────────────
@desk_only
def fleet_console(request):
    """Every car, with whatever is wrong with it shown first."""
    cars = list(Vehicle.objects.select_related('entity')
                .annotate(trips=Count('assignments', distinct=True),
                          open_events=Count('events', distinct=True,
                                            filter=Q(events__is_settled=False,
                                                     events__kind__in=[
                                                         VehicleEvent.Kind.FINE,
                                                         VehicleEvent.Kind.ACCIDENT])))
                .order_by('entity__code', 'make', 'model'))
    return render(request, 'services/fleet_console.html', {
        'cars': cars,
        'needs_attention': [c for c in cars if c.alerts],
        'entities': Entity.objects.order_by('code'),
        'statuses': Vehicle.Status.choices,
    })


@desk_only
def fleet_vehicle(request, pk):
    """One car: its details, its history, its bookings."""
    v = get_object_or_404(Vehicle.objects.select_related('entity'), pk=pk)
    return render(request, 'services/fleet_vehicle.html', {
        'v': v,
        'events': v.events.select_related('driver')[:100],
        'logs': v.trip_logs.select_related('driver', 'booking')[:100],
        'spend': v.events.aggregate(total=Sum('cost'))['total'],
        'entities': Entity.objects.order_by('code'),
        'statuses': Vehicle.Status.choices,
        'kinds': VehicleEvent.Kind.choices,
        'drivers': Employee.objects.filter(is_active=True).order_by('full_name'),
        'upcoming': (v.assignments.filter(
            booking__status__in=VehicleBooking.HOLDS_CAR,
            booking__date__gte=date.today())
            .select_related('booking', 'booking__booked_by')
            .order_by('booking__date')[:20]),
    })


def _vehicle_fields(request, v):
    """Read a car off a posted form.

    Blank dates and numbers become None rather than the empty string, which a
    DateField refuses and an IntegerField turns into a 500.
    """
    def txt(k, limit=200):
        return (request.POST.get(k) or '').strip()[:limit]

    def day(k):
        raw = request.POST.get(k)
        try:
            return datetime.strptime(raw, '%Y-%m-%d').date() if raw else None
        except ValueError:
            return None

    def num(k):
        raw = request.POST.get(k)
        try:
            return int(raw) if raw not in (None, '') else None
        except ValueError:
            return None

    v.entity_id = request.POST.get('entity') or v.entity_id
    v.make, v.model, v.plate = txt('make', 60), txt('model', 60), txt('plate', 30)
    v.seats = num('seats') or v.seats or 5
    v.status = request.POST.get('status') or Vehicle.Status.ACTIVE
    v.notes = (request.POST.get('notes') or '').strip()
    v.insurer, v.insurance_policy = txt('insurer', 120), txt('insurance_policy', 60)
    v.insurance_expiry = day('insurance_expiry')
    v.registration_expiry = day('registration_expiry')
    v.odometer = num('odometer')
    v.next_service_due = day('next_service_due')
    v.next_service_km = num('next_service_km')
    # Not stored as it arrived. Renders, screenshots and drawings come in at
    # three different aspect ratios, and five cars in a row at three ratios
    # never look like one fleet however good each picture is on its own.
    if request.FILES.get('photo'):
        from .fleet_photo import normalise
        v._incoming_photo = normalise(request.FILES['photo'])
    return v


@desk_only
@require_POST
def fleet_vehicle_save(request, pk=0):
    v = Vehicle() if not pk else get_object_or_404(Vehicle, pk=pk)
    _vehicle_fields(request, v)
    if not v.entity_id or not v.make:
        messages.error(request, 'A car needs a division and a make.')
        return redirect('services:fleet_console')
    v.save()
    # After the save, so a brand new car has a pk to name its file with.
    if getattr(v, '_incoming_photo', None) is not None:
        v.photo.save('car-%d.png' % v.pk, v._incoming_photo, save=True)
    messages.success(request, v.label + ' saved.')
    return redirect('services:fleet_vehicle', pk=v.pk)


@desk_only
@require_POST
def fleet_vehicle_retire(request, pk):
    """Retired, not deleted.

    A car that has carried people and collected fines is referred to by its
    bookings, its trip logs and its events. Deleting the row would either take
    that history with it or be refused outright, so retiring is the only honest
    option: it disappears from every form and keeps its past.
    """
    v = get_object_or_404(Vehicle, pk=pk)
    v.status = Vehicle.Status.RETIRED
    v.save(update_fields=['status'])
    messages.success(request, v.label + ' retired. Its history is kept.')
    return redirect('services:fleet_console')


@desk_only
@require_POST
def fleet_event_save(request, pk=0):
    """A service, a fine, an accident, a renewal."""
    e = VehicleEvent() if not pk else get_object_or_404(VehicleEvent, pk=pk)
    e.vehicle_id = request.POST.get('vehicle') or e.vehicle_id
    e.kind = request.POST.get('kind') or VehicleEvent.Kind.OTHER
    try:
        e.happened_on = datetime.strptime(request.POST['happened_on'], '%Y-%m-%d').date()
    except (KeyError, ValueError):
        messages.error(request, 'That needs a date.')
        return redirect('services:fleet_vehicle', pk=e.vehicle_id)
    e.title = (request.POST.get('title') or '').strip()[:200]
    e.detail = (request.POST.get('detail') or '').strip()
    e.cost = request.POST.get('cost') or None
    e.currency = (request.POST.get('currency') or 'JOD')[:8]
    e.reference = (request.POST.get('reference') or '').strip()[:80]
    e.vendor = (request.POST.get('vendor') or '').strip()[:120]
    e.driver_id = request.POST.get('driver') or None
    e.is_settled = request.POST.get('is_settled') in ('1', 'on', 'true')
    if request.FILES.get('document'):
        e.document = request.FILES['document']
    e.recorded_by = request.user
    e.save()

    # A renewal is the event that moves the date on the car, so the two cannot
    # drift apart: record the renewal, and the car stops reading as overdue.
    v = e.vehicle
    moved = None
    if e.kind == VehicleEvent.Kind.INSURANCE and request.POST.get('new_expiry'):
        v.insurance_expiry, moved = _as_date(request.POST['new_expiry']), 'insurance_expiry'
    elif e.kind == VehicleEvent.Kind.REGISTRATION and request.POST.get('new_expiry'):
        v.registration_expiry, moved = _as_date(request.POST['new_expiry']), 'registration_expiry'
    elif e.kind == VehicleEvent.Kind.SERVICE and request.POST.get('next_service_due'):
        v.next_service_due, moved = _as_date(request.POST['next_service_due']), 'next_service_due'
    if moved:
        v.save(update_fields=[moved])

    messages.success(request, 'Recorded.')
    return redirect('services:fleet_vehicle', pk=e.vehicle_id)


def _as_date(raw):
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


@desk_only
@require_POST
def fleet_event_delete(request, pk):
    e = get_object_or_404(VehicleEvent, pk=pk)
    vid = e.vehicle_id
    e.delete()
    messages.success(request, 'Removed.')
    return redirect('services:fleet_vehicle', pk=vid)


@desk_only
@require_POST
def fleet_trip_log_save(request, pk=0):
    """What the car actually did, as against what was booked."""
    log = VehicleTripLog() if not pk else get_object_or_404(VehicleTripLog, pk=pk)
    log.vehicle_id = request.POST.get('vehicle') or log.vehicle_id
    log.booking_id = request.POST.get('booking') or None
    log.driver_id = request.POST.get('driver') or None

    def when(k):
        raw = request.POST.get(k)
        try:
            return datetime.strptime(raw, '%Y-%m-%dT%H:%M') if raw else None
        except (TypeError, ValueError):
            return None

    log.left_at, log.returned_at = when('left_at'), when('returned_at')
    for f in ('odometer_out', 'odometer_in'):
        raw = request.POST.get(f)
        setattr(log, f, int(raw) if raw else None)
    log.fuel_cost = request.POST.get('fuel_cost') or None
    log.notes = (request.POST.get('notes') or '').strip()
    log.logged_by = request.user
    log.save()
    messages.success(request, 'Trip logged.')
    return redirect('services:fleet_vehicle', pk=log.vehicle_id)


# ══════════════════════════════════════════════════════════════════════════
#  THE MAP TILES
#
#  The browser asked OpenStreetMap for tiles directly and got 403 "App is not
#  following the tile usage policy" painted across the map. OSM's tile servers
#  are run by volunteers and they are right to block an unidentified app: from
#  their side, an office full of people behind one IP with no way to tell who
#  it is looks exactly like something to cut off.
#
#  CARTO's basemaps, the usual free alternative, now want an API key on every
#  style, so that is not a way out either.
#
#  So the Hub fetches the tiles itself and hands them on. That satisfies the
#  policy properly rather than dodging it:
#
#    · every request carries a User-Agent naming AJi and an address to write
#      to, which is the thing OSM actually asks for;
#    · every tile is cached on disk here, so the same street is fetched from
#      them once and served from AJi for a month afterwards — the load they
#      see is a fraction of the map being looked at;
#    · nothing bulk-downloads. A tile is fetched when somebody looks at it.
#
#  It also means an employee's browser never talks to a third party at all,
#  which is the same reason the address search is proxied.
# ══════════════════════════════════════════════════════════════════════════
TILE_CACHE = os.path.join(settings.BASE_DIR, '.tilecache')
TILE_MAX_AGE = 60 * 60 * 24 * 30                 # a month, in the browser too
TILE_UPSTREAM = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
#: A transparent PNG, served when the upstream is unreachable. A missing tile
#: has to be a hole in the picture and never an error page, or one dead tile
#: takes the whole screen down with it.
BLANK_PNG = base64.b64decode(
    b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk'
    b'YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==')


@login_required
def tile_proxy(request, z, x, y):
    """One map tile, fetched once and then served from here.

    Behind login like everything else: these are only ever requested by the
    booking screens, and leaving them open would turn the Hub into a public
    tile server, which is precisely the thing OSM is trying to prevent.
    """
    # Refuse anything outside the pyramid before it reaches the network. z, x
    # and y arrive as integers from the URL pattern, so this is about range
    # rather than type: a request for tile 9e9 is not a typo, it is a probe.
    if not (0 <= z <= 19) or not (0 <= x < 2 ** z) or not (0 <= y < 2 ** z):
        return HttpResponseBadRequest('no such tile')

    path = os.path.join(TILE_CACHE, str(z), str(x), f'{y}.png')
    if os.path.exists(path):
        with open(path, 'rb') as fh:
            data = fh.read()
        return _tile_response(data, cached=True)

    req = urllib.request.Request(
        TILE_UPSTREAM.format(z=z, x=x, y=y),
        headers={'User-Agent': 'AJi Hub intranet (intranet@ajigroup.com)',
                 'Accept': 'image/png,image/*'})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = r.read()
    except Exception:
        # Do not cache a failure: the next person to look at this street should
        # try again rather than inherit a blank square for a month.
        return _tile_response(BLANK_PNG, cached=False, max_age=60)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.part'                 # written aside, then moved into place,
    with open(tmp, 'wb') as fh:          # so a half-written tile is never read
        fh.write(data)
    os.replace(tmp, path)
    return _tile_response(data, cached=False)


def _tile_response(data, cached, max_age=TILE_MAX_AGE):
    resp = HttpResponse(data, content_type='image/png')
    resp['Cache-Control'] = f'public, max-age={max_age}'
    resp['X-Tile-Cache'] = 'hit' if cached else 'miss'
    return resp


# ══════════════════════════════════════════════════════════════════════════
#  THE USAGE LOG
#
#  Every trip the fleet has ever made, in one filterable table, and the same
#  table out as a spreadsheet. The filters are the questions people actually
#  ask of a fleet — who had the car, which car, when, which division, was it
#  approved, how far did it go — and the export carries whatever the filters
#  left on screen, because a spreadsheet of everything is not an answer to any
#  of those questions.
#
#  Read-only, on purpose. Bookings are changed on the booking's own page,
#  where the availability check runs; a log that could be edited would be a
#  second way to move a trip and the two would disagree.
# ══════════════════════════════════════════════════════════════════════════
LOG_STATUSES = [('', 'Any outcome')] + list(VehicleBooking.Status.choices)


def _log_queryset(request):
    """The rows, and the filters that produced them."""
    qs = (_scoped(request.user)
          .select_related('entity', 'booked_by', 'reviewed_by')
          .prefetch_related('assignments__vehicle', 'assignments__driver',
                            'trip_logs'))

    f = {
        'q': (request.GET.get('q') or '').strip(),
        'car': request.GET.get('car') or '',
        'entity': request.GET.get('entity') or '',
        'user': request.GET.get('user') or '',
        'approver': request.GET.get('approver') or '',
        'driver': request.GET.get('driver') or '',
        'status': request.GET.get('status') or '',
        'from': request.GET.get('from') or '',
        'to': request.GET.get('to') or '',
    }

    if f['q']:
        qs = qs.filter(Q(from_address__icontains=f['q'])
                       | Q(to_address__icontains=f['q'])
                       | Q(purpose__icontains=f['q'])
                       | Q(notes__icontains=f['q'])
                       | Q(booked_by__first_name__icontains=f['q'])
                       | Q(booked_by__last_name__icontains=f['q'])
                       | Q(booked_by__username__icontains=f['q']))
    if f['car'].isdigit():
        qs = qs.filter(assignments__vehicle_id=int(f['car']))
    if f['entity'].isdigit():
        qs = qs.filter(entity_id=int(f['entity']))
    if f['user'].isdigit():
        qs = qs.filter(booked_by_id=int(f['user']))
    if f['approver'].isdigit():
        qs = qs.filter(reviewed_by_id=int(f['approver']))
    if f['driver'].isdigit():
        qs = qs.filter(assignments__driver_id=int(f['driver']))
    if f['status']:
        qs = qs.filter(status=f['status'])
    for key, lookup in (('from', 'date__gte'), ('to', 'date__lte')):
        if f[key]:
            try:
                qs = qs.filter(**{lookup: datetime.strptime(f[key], '%Y-%m-%d').date()})
            except ValueError:
                f[key] = ''

    # Filtering on a car or a driver joins the assignment rows, and a booking
    # with two cars would otherwise appear twice.
    return qs.distinct().order_by('-date', '-departure_time'), f


@desk_only
def car_log(request):
    """The whole history, filtered."""
    qs, f = _log_queryset(request)
    rows = list(qs[:500])

    km = sum((l.distance_km or 0) for b in rows for l in b.trip_logs.all())
    return render(request, 'services/car_log.html', {
        'rows': rows,
        'total': qs.count(),
        'capped': qs.count() > len(rows),
        'km': km,
        'f': f,
        'cars': Vehicle.objects.select_related('entity').order_by(
            'entity__code', 'make', 'model'),
        'entities': Entity.objects.filter(vehicle_bookings__isnull=False)
                    .distinct().order_by('code'),
        'people': User.objects.filter(vehicle_bookings__isnull=False)
                  .distinct().order_by('first_name', 'username'),
        # Only people who have actually decided something: a list of everyone
        # who could approve is a list of names that return nothing.
        'approvers': User.objects.filter(vehicle_bookings_reviewed__isnull=False)
                     .distinct().order_by('first_name', 'username'),
        'drivers': Employee.objects.filter(driving_assignments__isnull=False)
                   .distinct().order_by('full_name'),
        'statuses': LOG_STATUSES,
        'querystring': request.GET.urlencode(),
    })


#: One row of the export. Kept beside the view so the column headings and the
#: values they carry are written in the same place and cannot drift apart.
EXPORT_COLUMNS = [
    ('Booking', lambda b: b.pk),
    ('Status', lambda b: b.get_status_display()),
    ('Division', lambda b: b.entity.code),
    ('Requested by', lambda b: b.booked_by.get_full_name() or b.booked_by.username),
    ('Date out', lambda b: b.date),
    ('Time out', lambda b: b.departure_time.strftime('%H:%M')),
    ('Date back', lambda b: b.return_date),
    ('Time back', lambda b: b.return_time.strftime('%H:%M')),
    ('Hours', lambda b: round((b.duration_minutes or 0) / 60.0, 2)),
    ('Multi-day', lambda b: 'Yes' if b.is_multi_day else ''),
    ('From', lambda b: b.from_address),
    ('To', lambda b: b.to_address),
    ('Cars', lambda b: ', '.join(c.label for c in b.cars)),
    ('Plates', lambda b: ', '.join(c.plate for c in b.cars if c.plate)),
    ('Drivers', lambda b: ', '.join(a.driver.full_name
                                    for a in b.assignments.all() if a.driver_id)),
    ('Driver requested', lambda b: 'Yes' if b.driver_required else ''),
    ('Travelling', lambda b: b.attendees),
    ('Purpose', lambda b: b.purpose),
    ('Notes', lambda b: b.notes),
    ('Odometer out', lambda b: ', '.join(str(l.odometer_out) for l in b.trip_logs.all()
                                         if l.odometer_out is not None)),
    ('Odometer in', lambda b: ', '.join(str(l.odometer_in) for l in b.trip_logs.all()
                                        if l.odometer_in is not None)),
    ('Kilometres', lambda b: sum((l.distance_km or 0) for l in b.trip_logs.all()) or ''),
    ('Decided by', lambda b: (b.reviewed_by.get_full_name() or b.reviewed_by.username)
                             if b.reviewed_by_id else ''),
    ('Decided at', lambda b: b.reviewed_at.replace(tzinfo=None) if b.reviewed_at else ''),
    ('Desk note', lambda b: b.review_comment),
    ('Requested at', lambda b: b.created_at.replace(tzinfo=None)),
]


@desk_only
def car_log_export(request):
    """The filtered log as a spreadsheet.

    Whatever the filters left on screen is what comes out: an export that
    ignored them would answer a question nobody asked, and the person would
    have to filter it again at the other end.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    qs, f = _log_queryset(request)

    wb = Workbook()
    ws = wb.active
    ws.title = 'Car usage'

    head_fill = PatternFill('solid', fgColor='111111')
    head_font = Font(bold=True, color='FCD700', size=10)
    ws.append([name for name, _ in EXPORT_COLUMNS])
    for cell in ws[1]:
        cell.fill, cell.font = head_fill, head_font
        cell.alignment = Alignment(vertical='center')
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = f'A1:{get_column_letter(len(EXPORT_COLUMNS))}1'

    n = 0
    # A chunk size is compulsory once the queryset prefetches, and it is the
    # right thing anyway: the rows are streamed rather than all held at once,
    # so a year of the fleet does not have to fit in memory to be exported.
    for b in qs.iterator(chunk_size=200):
        ws.append([get(b) for _, get in EXPORT_COLUMNS])
        n += 1

    # Column widths from the content, capped: a Notes column as wide as its
    # longest note makes every other column unreadable.
    for i, (name, _) in enumerate(EXPORT_COLUMNS, start=1):
        longest = max([len(name)]
                      + [len(str(ws.cell(r, i).value or ''))
                         for r in range(2, min(ws.max_row, 200) + 1)])
        ws.column_dimensions[get_column_letter(i)].width = min(max(longest + 2, 10), 46)

    # A second sheet saying what was filtered, so a spreadsheet that has been
    # emailed on still explains what it is a list of.
    meta = wb.create_sheet('Filters')
    meta.append(['AJi Hub — car usage log'])
    meta['A1'].font = Font(bold=True, size=12)
    meta.append(['Exported', timezone.now().strftime('%d %b %Y %H:%M')])
    meta.append(['Exported by', request.user.get_full_name() or request.user.username])
    meta.append(['Rows', n])
    meta.append([])
    labels = {'q': 'Search', 'car': 'Car', 'entity': 'Division', 'user': 'Requested by',
              'driver': 'Driver', 'status': 'Outcome', 'from': 'From date',
              'to': 'To date'}
    pretty = {
        'car': lambda v: str(Vehicle.objects.filter(pk=v).first() or v),
        'entity': lambda v: str(Entity.objects.filter(pk=v).first() or v),
        'user': lambda v: str(User.objects.filter(pk=v).first() or v),
        'driver': lambda v: str(Employee.objects.filter(pk=v).first() or v),
    }
    any_filter = False
    for key, label in labels.items():
        if f.get(key):
            meta.append([label, pretty.get(key, str)(f[key])])
            any_filter = True
    if not any_filter:
        meta.append(['Filters', 'None — this is the whole log'])
    meta.column_dimensions['A'].width = 18
    meta.column_dimensions['B'].width = 46

    stamp = timezone.now().strftime('%Y-%m-%d')
    resp = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    resp['Content-Disposition'] = f'attachment; filename="AJi car usage {stamp}.xlsx"'
    wb.save(resp)
    return resp


# ══ who approves what ═════════════════════════════════════════════════════
#  AJiBE's cars are not AJiWE's to give away. The routing has always been per
#  division in the database; this screen is so that changing it does not mean
#  opening Django admin, which nobody at the desk should have to do.

def _person_label(user):
    emp = Employee.objects.filter(user=user).first()
    name = (emp.full_name if emp else '') or user.get_full_name() or user.username
    return f'{name} <{user.email}>' if user.email else name


def _user_from_field(text):
    """Find the person behind 'Nael Shunnar <nael@…>', an email, or a name."""
    text = (text or '').strip()
    if not text:
        return None
    if '<' in text and '>' in text:
        text = text[text.rfind('<') + 1:text.rfind('>')].strip()
    if '@' in text:
        return (User.objects.filter(is_active=True, email__iexact=text).first()
                or User.objects.filter(is_active=True,
                                       employee__email__iexact=text).first())
    emp = Employee.objects.filter(full_name__iexact=text,
                                  user__isnull=False,
                                  user__is_active=True).first()
    return emp.user if emp else None


@desk_only
@require_POST
def fleet_approver_save(request):
    """Add, remove, or silence one approver."""
    action = request.POST.get('action')

    if action == 'remove':
        row = FleetApprover.objects.filter(pk=request.POST.get('row')).first()
        if row:
            who = _person_label(row.user).split(' <')[0]
            code = row.entity.code
            row.delete()
            messages.success(request, f'{who} no longer approves {code} cars.')

    elif action == 'email':
        row = get_object_or_404(FleetApprover, pk=request.POST.get('row'))
        row.notify = not row.notify
        row.save(update_fields=['notify'])
        messages.success(
            request,
            ('Emails on for ' if row.notify else 'Emails off for ')
            + _person_label(row.user).split(' <')[0]
            + f' ({row.entity.code}). '
            + ('' if row.notify else 'They still see requests in the Hub.'))

    elif action == 'add':
        entity = get_object_or_404(Entity, pk=request.POST.get('entity'))
        # The picker posts one `person` per chosen person: user ids from the
        # search, or typed text from the plain box if scripting is off.
        wanted, unknown = [], []
        for raw in request.POST.getlist('person'):
            raw = (raw or '').strip()
            if not raw:
                continue
            user = (User.objects.filter(pk=raw, is_active=True).first()
                    if raw.isdigit() else _user_from_field(raw))
            if user:
                wanted.append(user)
            else:
                unknown.append(raw)

        added, already, no_email = [], 0, []
        notify_them = request.POST.get('notify') != '0'
        for user in wanted:
            if not (user.email or '').strip():
                no_email.append(_person_label(user))
                continue
            _, made = FleetApprover.objects.get_or_create(
                entity=entity, user=user, defaults={'notify': notify_them})
            if made:
                added.append(_person_label(user).split(' <')[0])
            else:
                already += 1

        if added:
            messages.success(
                request,
                ('%s now approve%s %s cars' % (
                    ', '.join(added), '' if len(added) > 1 else 's', entity.code))
                + ('.' if notify_them else ', without emails.'))
        if already:
            messages.info(request, '%d %s already on that list.'
                          % (already, 'were' if already > 1 else 'was'))
        for label in no_email:
            messages.error(request, label + ' has no email address on file, so '
                                            'they cannot be told by email.')
        for raw in unknown:
            messages.error(request, 'No active person matches “%s”. Pick a name '
                                    'from the list.' % raw[:40])
        if not (added or already or no_email or unknown):
            messages.error(request, 'Nobody was chosen.')

    return redirect('services:fleet_approvers')


@desk_only
def fleet_approvers(request):
    """One list of approvers per division, and who gets asked if it is empty."""
    rows = (FleetApprover.objects.select_related('entity', 'user')
            .filter(user__is_active=True).order_by('order', 'pk'))
    by_entity = {}
    for r in rows:
        by_entity.setdefault(r.entity_id, []).append(
            {'row': r, 'name': _person_label(r.user).split(' <')[0],
             'email': r.user.email})

    fallback = [{'name': (u.get_full_name() or u.username), 'email': u.email}
                for u in fleet.desk_users().order_by('first_name', 'username')]

    # A division belongs on this page if it owns a car or if somebody has
    # already been named for it. HDP has no car in the Hub yet and the day it
    # gets one is not the day to start looking for who approves it.
    shown = {e.pk: e for e in _entities_with_cars()}
    for r in rows:
        shown.setdefault(r.entity_id, r.entity)

    divisions = []
    for e in sorted(shown.values(), key=lambda x: x.code):
        divisions.append({
            'entity': e, 'people': by_entity.get(e.pk, []),
            'cars': Vehicle.objects.filter(entity=e)
                    .exclude(status=Vehicle.Status.RETIRED).count(),
            'waiting': VehicleBooking.objects.filter(
                entity=e, status=VehicleBooking.Status.PENDING).count(),
        })
    others = Entity.objects.exclude(pk__in=shown).order_by('code')

    people = (Employee.objects.filter(is_active=True, user__isnull=False,
                                      user__is_active=True)
              .select_related('user').order_by('full_name'))
    return render(request, 'services/fleet_approvers.html', {
        'divisions': divisions,
        'others': others,
        'fallback': fallback,
        'people': [{'label': f'{p.full_name} <{p.user.email}>', 'name': p.full_name,
                    'email': p.user.email}
                   for p in people if p.user.email],
    })


# ══ the odometer, per trip ════════════════════════════════════════════════
#  The reading is taken by whoever hands over the keys, which is the desk, and
#  it belongs to the trip rather than to the car: "the Sportage is on 81,402"
#  answers nothing, while "it went out on 81,180 and came back on 81,402"
#  answers what the trip cost, what the driver did, and when the next service
#  falls due. One row per car per booking, so a trip with two cars has two
#  readings and neither is lost in an average.

@desk_only
@require_POST
def car_trip_save(request, pk):
    """Record a car out or a car back on one booking."""
    booking = get_object_or_404(VehicleBooking, pk=pk)
    if not fleet.can_review(request.user, booking):
        raise Http404

    assignment = booking.assignments.filter(
        vehicle_id=request.POST.get('vehicle')).select_related('vehicle').first()
    if not assignment:
        messages.error(request, 'That car is not on this booking.')
        return redirect('services:car_request_detail', pk=pk)
    car = assignment.vehicle

    def reading(key):
        raw = (request.POST.get(key) or '').replace(',', '').strip()
        if not raw:
            return None
        try:
            return max(0, int(float(raw)))
        except ValueError:
            raise ValueError('The odometer has to be a number of kilometres.')

    def when(key, fallback=None):
        raw = request.POST.get(key)
        if not raw:
            return fallback
        for fmt in ('%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M'):
            try:
                return datetime.strptime(raw, fmt)
            except ValueError:
                continue
        return fallback

    try:
        out_km, in_km = reading('odometer_out'), reading('odometer_in')
    except ValueError as e:
        messages.error(request, str(e))
        return redirect('services:car_request_detail', pk=pk)

    log, _ = VehicleTripLog.objects.get_or_create(
        booking=booking, vehicle=car,
        defaults={'driver': assignment.driver, 'logged_by': request.user})

    # Check the two readings against each other as they will BE, not only as
    # they were posted: the desk records the car going out in the morning and
    # coming back in the afternoon, so the pair almost never arrives together
    # and comparing only what is in this one request checks nothing.
    final_out = out_km if out_km is not None else log.odometer_out
    final_in = in_km if in_km is not None else log.odometer_in
    if final_out is not None and final_in is not None and final_in < final_out:
        messages.error(request, f'{final_in:,} back against {final_out:,} out. '
                                'A car cannot come back on fewer kilometres.')
        return redirect('services:car_request_detail', pk=pk)

    if out_km is not None:
        log.odometer_out = out_km
    if in_km is not None:
        log.odometer_in = in_km
    log.left_at = when('left_at', log.left_at or booking.start)
    log.returned_at = when('returned_at', log.returned_at)
    if in_km is not None and not log.returned_at:
        # A reading back without a time is still a car that came back.
        log.returned_at = timezone.now()
    log.driver = assignment.driver or log.driver
    log.notes = (request.POST.get('note') or log.notes or '').strip()
    log.logged_by = request.user
    log.save()

    if log.distance_km is not None:
        messages.success(request, f'{car.make} {car.model}: {log.distance_km:,} km '
                                  f'on this trip. The car now reads '
                                  f'{car.odometer or log.odometer_in:,}.')
    else:
        messages.success(request, f'{car.make} {car.model}: reading saved.')
    return redirect('services:car_request_detail', pk=pk)


@desk_only
def fleet_people_api(request):
    """Search the staff directory, for the approver picker.

    Reads Employee rather than User because Employee is where the names, roles
    and divisions actually are: "Mariam Kiswani, Finance Manager, AJiWE" is
    findable, and "mariam.kiswani.1846" is not.
    """
    q = (request.GET.get('q') or '').strip()
    rows = (Employee.objects
            .filter(is_active=True, user__isnull=False, user__is_active=True)
            .exclude(user__email='')
            .select_related('user', 'entity', 'department'))
    if q:
        rows = rows.filter(Q(full_name__icontains=q)
                           | Q(user__email__icontains=q)
                           | Q(role__icontains=q)
                           | Q(emp_code__icontains=q))
    rows = rows.order_by('full_name')[:25]
    return JsonResponse({'people': [{
        'id': e.user_id,
        'name': e.full_name,
        'email': e.user.email,
        'role': e.role or '',
        'entity': e.entity.code if e.entity_id else '',
    } for e in rows]})
