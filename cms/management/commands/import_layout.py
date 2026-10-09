"""Load a layout exported by `export_layout`, replacing the one already here.

Replacing, not merging. A plain `loaddata` writes the rows in the fixture and
leaves every other row alone, which on a server holding the seeded default would
add this machine's blocks *beside* the seeded ones: two news lists, an "Official
events" card nobody asked for, and a homepage longer than either version. So the
target page's sections and the whole menu are cleared first, inside one
transaction, and the fixture is written into the space.

Only the layout and the menu. The news, the people, the uploads and the awards on
the far side are untouched, which is the point: a server has real content and
this is only about where the blocks sit.
"""
import os

from django.core import serializers
from django.core.management.base import BaseCommand
from django.db import transaction

from cms.models import NavItem, Page, Section, SectionItem, SiteBranding

DEFAULT_IN = os.path.join('docs', 'layout.json')


class Command(BaseCommand):
    help = 'Replace the homepage layout and the navigation from a fixture.'

    def add_arguments(self, parser):
        parser.add_argument('--file', default=DEFAULT_IN,
                            help=f'The fixture to read. Default {DEFAULT_IN}')
        parser.add_argument('--dry-run', action='store_true',
                            help='Say what would change and stop.')

    def handle(self, *args, **options):
        path = options['file']
        if not os.path.exists(path):
            self.stderr.write(self.style.ERROR(f'No such file: {path}'))
            return

        with open(path, encoding='utf-8') as fh:
            objects = list(serializers.deserialize('json', fh.read()))

        pages = [o.object for o in objects if isinstance(o.object, Page)]
        if not pages:
            self.stderr.write(self.style.ERROR('That fixture holds no page.'))
            return
        page = pages[0]

        incoming = {
            'sections': sum(isinstance(o.object, Section) for o in objects),
            'items': sum(isinstance(o.object, SectionItem) for o in objects),
            'nav': sum(isinstance(o.object, NavItem) for o in objects),
        }
        here = Page.objects.filter(bound_path=page.bound_path).first()
        existing = {
            'sections': Section.objects.filter(page=here).count() if here else 0,
            'nav': NavItem.objects.count(),
        }

        self.stdout.write(f'  page      {page.title} ({page.bound_path})')
        self.stdout.write(f'  sections  {existing["sections"]} here '
                          f'-> {incoming["sections"]} incoming')
        self.stdout.write(f'  nav       {existing["nav"]} here '
                          f'-> {incoming["nav"]} incoming')
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('  dry run, nothing changed'))
            return

        with transaction.atomic():
            if here is not None:
                # Sections cascade to their own items, so this clears both.
                Section.objects.filter(page=here).delete()
            NavItem.objects.all().delete()
            SiteBranding.objects.all().delete()
            # Saved in the order the fixture holds them, which is page, then
            # sections, then items, then nav. Parent rows therefore land before
            # the rows that point at them.
            for obj in objects:
                obj.save()

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'  done. {Section.objects.filter(page__bound_path=page.bound_path).count()} sections, '
            f'{NavItem.objects.count()} menu entries.'))
