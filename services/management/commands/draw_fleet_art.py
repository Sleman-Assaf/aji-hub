# -*- coding: utf-8 -*-
"""Draw a side profile for every car, to that model's own proportions.

Photographs of the real cars would be better and nobody has taken them yet, so
each car's page will take an upload and this command leaves alone any car that
already has one. Stock or press photography is not an option: it belongs to the
manufacturer, and a picture of somebody else's Sportage under a heading that
says AJi Water & Environment is a small lie that looks like one.

The drawings themselves live in services/fleet_art.py, which holds a profile per
model — real length, wheelbase, height and wheel diameter, scaled to a common
canvas. That is what makes an Outlander read as taller than a Sportage and a
Pickup as longer than a Lancer, instead of all four being the same box with
different windows.

    python manage.py draw_fleet_art
    python manage.py draw_fleet_art --force          # redraw everything
    python manage.py draw_fleet_art --out ./svg      # keep the SVGs too
"""
import os
import warnings

from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand

from services.fleet_art import CANVAS_W, build, profile_for
from services.models import Vehicle

#: Rendered at twice the artboard, so the pictures stay sharp on a good screen.
PIXEL_WIDTH = CANVAS_W * 2


class Command(BaseCommand):
    help = 'Draw a side profile for every car that has no picture.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
                            help='Redraw cars that already have a picture.')
        parser.add_argument('--out', default='',
                            help='Also write the SVGs to this directory.')

    def handle(self, *args, **opts):
        warnings.filterwarnings('ignore')
        import pymupdf

        done = skipped = 0
        for v in Vehicle.objects.select_related('entity').order_by('pk'):
            if v.photo and not opts['force']:
                skipped += 1
                continue
            profile = profile_for(v.make, v.model)
            svg = build(profile)

            if opts['out']:
                os.makedirs(opts['out'], exist_ok=True)
                name = f'{v.pk}-{profile.name.lower().replace(" ", "-")}.svg'
                with open(os.path.join(opts['out'], name), 'w',
                          encoding='utf-8') as fh:
                    fh.write(svg)

            doc = pymupdf.open('svg', svg.encode('utf-8'))
            pix = doc[0].get_pixmap(
                matrix=pymupdf.Matrix(PIXEL_WIDTH / CANVAS_W,
                                      PIXEL_WIDTH / CANVAS_W), alpha=False)
            v.photo.save(f'car-{v.pk}.png', ContentFile(pix.tobytes('png')),
                         save=True)
            done += 1
            self.stdout.write(f'  {v.label:<30} drawn as {profile.name}')

        self.stdout.write(self.style.SUCCESS(
            f'\n  {done} drawn, {skipped} left alone (they have a picture already).'))
