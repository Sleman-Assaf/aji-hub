# -*- coding: utf-8 -*-
"""Put the car booking system in place: the role, the service card, the cars.

Idempotent — run it as often as you like. Nothing here overwrites a car that
already exists, so the seed fleet can be corrected in the Hub and this command
will leave the corrections alone.

    python manage.py setup_fleet
    python manage.py setup_fleet --no-cars      # role and service card only
"""
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction

from people.models import Entity
from services.models import (BOOKINGS_GROUP, Place, Service,
                             ServiceCategory, Vehicle)

LEGACY_GROUP = 'Reception Desk'

#: The fleet as it stands, read off the Outlook resource accounts. Make, model,
#: division, seats. Plates are deliberately absent — they are not in the
#: contacts and guessing them would put wrong numbers on an insurance screen.
#: The places every trip starts from or ends at. Coordinates decoded from the
#: Plus Codes on their Google listings — XV66+C2 and XV59+CH — which is exact,
#: where reading a pin off a screenshot is not.
PLACES = [
    ('AJi - Amman Office', 'AJi - Amman Office, Al Tabasheer St, Amman',
     31.96106, 35.86006, True),
    ('HDP (Overseas) Ltd', 'HDP (Overseas) Ltd, Park Plaza, Complex 31, '
                           'Ibn Khaldoun St, Amman',
     31.95856, 35.86894, False),
]

FLEET = [
    ('KIA',        'Sportage',  'AJiWE', 5),
    ('KIA',        'Sportage',  'AJiBE', 5),
    ('Mitsubishi', 'Pickup',    'AJiWE', 5),
    ('Mitsubishi', 'Outlander', 'AJiWE', 5),
    ('Mitsubishi', 'Lancer',    'AJiWE', 5),
]


class Command(BaseCommand):
    help = 'Create the Bookings role, the My AJi card, and the starting fleet.'

    def add_arguments(self, parser):
        parser.add_argument('--no-cars', action='store_true',
                            help='Skip the seed fleet.')

    @transaction.atomic
    def handle(self, *args, **opts):
        # ── the role ──────────────────────────────────────────────────────
        # Renamed rather than replaced: the people who run reception are the
        # people who run this, and a rename keeps every membership they already
        # have. Only the name was wrong — the job now covers cars as well.
        legacy = Group.objects.filter(name=LEGACY_GROUP).first()
        group = Group.objects.filter(name=BOOKINGS_GROUP).first()
        if legacy and not group:
            legacy.name = BOOKINGS_GROUP
            legacy.save()
            group = legacy
            self.stdout.write(f"  role    '{LEGACY_GROUP}' renamed to "
                              f"'{BOOKINGS_GROUP}' — {group.user_set.count()} members kept")
        elif legacy and group:
            # Both exist. Move everyone across rather than leaving half the
            # desk unable to approve anything.
            moved = 0
            for u in legacy.user_set.all():
                u.groups.add(group)
                u.groups.remove(legacy)
                moved += 1
            legacy.delete()
            self.stdout.write(f'  role    merged {moved} members into {BOOKINGS_GROUP}')
        else:
            group, made = Group.objects.get_or_create(name=BOOKINGS_GROUP)
            self.stdout.write(f"  role    '{BOOKINGS_GROUP}' "
                              + ('created' if made else 'already there')
                              + f' — {group.user_set.count()} members')

        # ── the card in My AJi ────────────────────────────────────────────
        cat = ServiceCategory.objects.filter(name='Workplace & Facilities').first()
        if not cat:
            self.stdout.write(self.style.WARNING(
                '  card    no "Workplace & Facilities" category — skipped'))
        else:
            svc, made = Service.objects.get_or_create(
                category=cat, service_key='car_booking',
                defaults=dict(
                    name='Car Booking',
                    description="Book one of AJi's company cars for a trip",
                    icon='facilities', order=15,
                ))
            self.stdout.write('  card    ' + ('created' if made else 'already there')
                              + f' — {svc.name}')

        # ── the places a trip starts from ─────────────────────────────────
        for order, (name, address, lat, lng, default) in enumerate(PLACES, start=1):
            place, made = Place.objects.get_or_create(
                name=name,
                defaults=dict(address=address, lat=lat, lng=lng,
                              kind=Place.Kind.OFFICE, order=order,
                              is_default_origin=default))
            self.stdout.write('  place   ' + ('added   ' if made else 'already ')
                              + f'{place.name}')

        # ── the cars ──────────────────────────────────────────────────────
        if opts['no_cars']:
            return
        made = skipped = 0
        for order, (make, model, code, seats) in enumerate(FLEET, start=1):
            entity = Entity.objects.filter(code=code).first()
            if not entity:
                self.stdout.write(self.style.WARNING(
                    f'  car     no entity {code} — {make} {model} skipped'))
                continue
            if Vehicle.objects.filter(entity=entity, make=make, model=model).exists():
                skipped += 1
                continue
            Vehicle.objects.create(entity=entity, make=make, model=model,
                                   seats=seats, order=order)
            made += 1
        self.stdout.write(f'  fleet   {made} added, {skipped} already there, '
                          f'{Vehicle.objects.count()} in total')
        self.stdout.write(self.style.SUCCESS(
            '\n  Add the plates, insurance and service dates in the Hub: '
            'My AJi > Car Booking > Fleet.'))
