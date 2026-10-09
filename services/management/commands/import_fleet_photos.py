# -*- coding: utf-8 -*-
"""Drop car pictures in a folder and this puts them on the right cars.

Files are matched on their name against the make, model and plate, so
`mitsubishi-l200.png`, `L200.jpg` and `AJiWE Lancer.png` all land where they
should. Whatever the picture arrived as, it is trimmed and fitted to the same
canvas as every other one — see fleet_photo.py for why that matters.

    python manage.py import_fleet_photos "C:\\path\\to\\pictures"
    python manage.py import_fleet_photos . --dry-run
"""
import os
import re

from django.core.management.base import BaseCommand, CommandError

from services.fleet_photo import save_for
from services.models import Vehicle

EXTS = {'.png', '.jpg', '.jpeg', '.webp'}


def tokens(text):
    """Lower-case words, so 'Mitsubishi_L200 (1).png' matches 'Mitsubishi L200'."""
    return [t for t in re.split(r'[^a-z0-9]+', text.lower()) if t]


class Command(BaseCommand):
    help = 'Import car pictures from a folder, matching them on their filename.'

    def add_arguments(self, parser):
        parser.add_argument('folder')
        parser.add_argument('--dry-run', action='store_true',
                            help='Say what would happen and change nothing.')
        parser.add_argument('--force', action='store_true',
                            help='Replace a picture that is already there.')

    def handle(self, *args, **opts):
        folder = opts['folder']
        if not os.path.isdir(folder):
            raise CommandError('No such folder: %s' % folder)

        cars = list(Vehicle.objects.select_related('entity'))
        if not cars:
            raise CommandError('There are no cars to match against.')

        files = sorted(f for f in os.listdir(folder)
                       if os.path.splitext(f)[1].lower() in EXTS)
        if not files:
            raise CommandError('No images in %s' % folder)

        used = set()
        for name in files:
            want = tokens(os.path.splitext(name)[0])
            best, score = None, 0
            for car in cars:
                if car.pk in used:
                    continue
                have = set(tokens('%s %s %s %s' % (
                    car.make, car.model, car.plate,
                    car.entity.code if car.entity_id else '')))
                # How many of the filename's words name this car. The model is
                # worth more than the make: two cars share "Mitsubishi", so a
                # tie on the make is broken by the specific word.
                hit = sum(3 if t == (car.model or '').lower() else 1
                          for t in want if t in have)
                if hit > score:
                    best, score = car, hit
            if not best:
                self.stdout.write(self.style.WARNING(
                    '  %-44s no car matches that name' % name))
                continue
            if best.photo and not opts['force']:
                self.stdout.write(
                    '  %-44s %s already has one, left alone' % (name, best.label))
                continue

            if opts['dry_run']:
                self.stdout.write('  %-44s -> %s' % (name, best.label))
            else:
                save_for(best, os.path.join(folder, name))
                self.stdout.write(self.style.SUCCESS(
                    '  %-44s -> %s' % (name, best.label)))
            used.add(best.pk)

        missing = [c for c in cars if not c.photo]
        if missing and not opts['dry_run']:
            self.stdout.write('\n  Still without a picture:')
            for c in missing:
                self.stdout.write('    %s' % c.label)
