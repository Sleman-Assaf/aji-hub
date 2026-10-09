# services/management/commands/seed_meeting_rooms.py
from django.core.management.base import BaseCommand

from services.models import MeetingRoom, Service, ServiceCategory

ROOMS = [
    # name, chairs, order
    ('GF1', 7, 0),
    ('GF2', 13, 1),
    ('4th Floor', 13, 2),
    ('Ayla', 10, 3),
    ('Petra', 10, 4),
    ('Rum (Conference Room)', 32, 5),
]


class Command(BaseCommand):
    help = 'Seeds the 6 meeting rooms and the Meeting Room Booking service under Workplace & Facilities (open to all users).'

    def handle(self, *args, **options):
        for name, chairs, order in ROOMS:
            room, created = MeetingRoom.objects.get_or_create(
                name=name, defaults={'chairs': chairs, 'order': order},
            )
            if not created and room.chairs != chairs:
                room.chairs = chairs
                room.save(update_fields=['chairs'])
            self.stdout.write(f"{'Created' if created else 'Found'} room: {name} ({chairs} chairs)")

        category, _ = ServiceCategory.objects.get_or_create(
            name='Workplace & Facilities',
            defaults={'category_type': ServiceCategory.CategoryType.SERVICES, 'icon': 'facilities', 'order': 4},
        )

        service, created = Service.objects.get_or_create(
            service_key='meeting_room_booking',
            defaults={
                'category': category,
                'name': 'Meeting Room Booking',
                'description': 'Book one of AJi\u2019s 6 meeting rooms',
                'group': None,  # open to all users
                'order': 0,
            },
        )
        self.stdout.write(f"{'Created' if created else 'Found'} service: Meeting Room Booking (under Workplace & Facilities, all users)")

        self.stdout.write(self.style.SUCCESS(
            '\nDone. 6 meeting rooms seeded and Meeting Room Booking is live under Workplace & Facilities for all users.'
        ))
