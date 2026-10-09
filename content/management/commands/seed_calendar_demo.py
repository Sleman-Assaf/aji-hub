# content/management/commands/seed_calendar_demo.py
"""Fill two months of the calendar with plausible events, meetings included.

Written so the calendar can be judged with something in it: the week view lays
events on a time axis and a block's height is its duration, which tells you
nothing at all when every event is 30 minutes or has no time on it.

Every row it creates is tagged in its description, so `--remove` can take exactly
these out again and leave real entries alone. Re-running replaces its own rows
rather than adding a second copy.
"""
import random
from datetime import date, time, timedelta

from django.core.management.base import BaseCommand

#: Stamped into every seeded description so they can be found again. Not the
#: title: titles are what people read, and prefixing them all would look wrong.
TAG = '[demo-seed]'

#: (title, event_type, minutes, earliest hour, location)
MEETINGS = [
    ('Weekly Design Review',        'meeting',  90, 10, 'Meeting Room 2'),
    ('Project Standup',             'meeting',  15,  9, 'Meeting Room 1'),
    ('Client Handover — MenaME',    'meeting', 120, 11, 'Boardroom'),
    ('Structural Coordination',     'meeting',  60, 13, 'Meeting Room 3'),
    ('MEP Clash Review',            'meeting',  45, 14, 'Meeting Room 2'),
    ('Tender Pricing Session',      'meeting', 180,  9, 'Boardroom'),
    ('One-to-one — Line Manager',   'meeting',  30, 15, 'Office'),
    ('Site Progress Call',          'meeting',  30,  8, 'Teams'),
    ('Budget Forecast Meeting',     'meeting',  75, 11, 'Boardroom'),
    ('Sprint Planning',             'meeting',  60, 10, 'Meeting Room 1'),
    ('Quality Audit Debrief',       'meeting',  45, 14, 'Meeting Room 3'),
    ('Recruitment Panel',           'meeting', 105, 10, 'Meeting Room 2'),
]

TRAINING = [
    ('LEED Fundamentals Workshop',  'training', 240,  9, 'Training Room'),
    ('Revit Advanced Modelling',    'training', 180, 13, 'Training Room'),
    ('Fire Safety Briefing',        'training',  60, 11, 'Auditorium'),
    ('PMP Exam Prep — Session 3',   'training', 120, 16, 'Training Room'),
]

COMPANY = [
    ('AJi Town Hall',               'company',  90, 12, 'Auditorium'),
    ('Long Service Awards',         'company', 120, 17, 'Auditorium'),
    ('New Joiners Welcome',         'company',  60, 10, 'Reception'),
]

#: All-day entries, to prove the time axis handles something with no duration.
ALL_DAY = [
    ('Eid al-Fitr Holiday',         'holiday'),
    ('Independence Day',            'holiday'),
    ('Office Closed — Maintenance', 'holiday'),
]


class Command(BaseCommand):
    help = 'Create two months of demo calendar events, or remove them again.'

    def add_arguments(self, parser):
        parser.add_argument('--remove', action='store_true',
                            help='Delete the demo events and create nothing.')
        parser.add_argument('--start', default='',
                            help='ISO date to start from. Defaults to the 1st of '
                                 'this month.')
        parser.add_argument('--seed', type=int, default=20260728,
                            help='Random seed, so a re-run produces the same '
                                 'calendar rather than a different one.')

    def handle(self, *args, **options):
        from content.models import CalendarEvent

        existing = CalendarEvent.objects.filter(description__contains=TAG)
        if options['remove']:
            n = existing.count()
            existing.delete()
            self.stdout.write(f'Removed {n} demo event(s). Real entries untouched.')
            return

        # Replace its own previous run so this is safe to repeat.
        if existing.exists():
            n = existing.count()
            existing.delete()
            self.stdout.write(f'Cleared {n} event(s) from an earlier run.')

        if options['start']:
            first = date.fromisoformat(options['start'])
        else:
            today = date.today()
            first = today.replace(day=1)

        rng = random.Random(options['seed'])
        made = 0
        pool = MEETINGS + TRAINING + COMPANY

        day = first
        end_of_range = first + timedelta(days=62)
        while day < end_of_range:
            # Weekends stay clear apart from the occasional company event, which
            # is what a real calendar looks like and makes the working week read
            # as the busy part.
            if day.weekday() in (4, 5):          # Fri, Sat — the AJi weekend
                if rng.random() < 0.12:
                    title, kind, mins, hour, loc = rng.choice(COMPANY)
                    self._event(CalendarEvent, day, title, kind, mins, hour, loc, rng)
                    made += 1
                day += timedelta(days=1)
                continue

            for _ in range(rng.choice([1, 1, 2, 2, 2, 3, 3, 4])):
                title, kind, mins, hour, loc = rng.choice(pool)
                self._event(CalendarEvent, day, title, kind, mins, hour, loc, rng)
                made += 1
            day += timedelta(days=1)

        # A few all-day entries spread across the range.
        for i, (title, kind) in enumerate(ALL_DAY):
            when = first + timedelta(days=9 + i * 19)
            CalendarEvent.objects.create(
                title=title, date=when, all_day=True, event_type=kind,
                location='', is_public=True,
                description=f'Demo data. {TAG}')
            made += 1

        # One deliberately long meeting and one very short one on the same day, so
        # the difference in height is impossible to miss.
        showcase = first + timedelta(days=(2 - first.weekday()) % 7 + 7)
        CalendarEvent.objects.create(
            title='Quarterly Business Review', date=showcase, all_day=False,
            time=time(9, 0), end_time=time(13, 0), event_type='meeting',
            location='Boardroom', is_public=True,
            description=f'Four hours, to show a long block. {TAG}')
        CalendarEvent.objects.create(
            title='Daily Check-in', date=showcase, all_day=False,
            time=time(14, 0), end_time=time(14, 15), event_type='meeting',
            location='Teams', is_public=True,
            description=f'Fifteen minutes, to show a short one. {TAG}')
        made += 2

        self.stdout.write(f'Created {made} demo event(s) from {first} '
                          f'to {end_of_range - timedelta(days=1)}.')
        self.stdout.write(f'Both extremes are on {showcase}: a 4-hour review and '
                          f'a 15-minute check-in.')
        self.stdout.write('Remove them again with: '
                          'manage.py seed_calendar_demo --remove')

    def _event(self, model, day, title, kind, mins, earliest, loc, rng):
        """One timed event, nudged so a re-run is varied but repeatable."""
        # Start on a quarter hour, within the working day, and never running past
        # 19:00 -- a block that overflowed the axis would be clipped.
        hour = min(earliest + rng.choice([0, 0, 1]), 17)
        minute = rng.choice([0, 0, 15, 30, 45])
        start = time(hour, minute)
        total = hour * 60 + minute + mins
        if total > 19 * 60:
            mins = max(15, 19 * 60 - (hour * 60 + minute))
        end_total = hour * 60 + minute + mins
        end = time(end_total // 60, end_total % 60)
        model.objects.create(
            title=title, date=day, all_day=False, time=start, end_time=end,
            event_type=kind, location=loc, is_public=True,
            description=f'Demo data — {mins} minutes. {TAG}')
