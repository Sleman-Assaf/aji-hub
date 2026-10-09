"""Export the homepage layout and the navigation, so a server can match here.

The arrangement of the homepage and the contents of the top menu are rows in the
database, not code. That is the whole point of the CMS: an editor rearranges the
homepage without a deploy. It also means a deploy carries none of it, so a server
running the same code as this machine still shows whatever layout its own
database holds, which for a fresh install is the seeded default from
`import_home` and a menu with no AJi Taqdeer in it.

This writes the layout and the menu to a fixture. `import_layout` loads it on the
other side. Content is deliberately not included: the news, the people, the
uploads and the awards on the server are the server's own, and this is only
about where the blocks sit and what the menu says.
"""
import json
import os

from django.core import serializers
from django.core.management.base import BaseCommand

from cms.models import NavItem, Page, Section, SectionItem, SiteBranding

DEFAULT_OUT = os.path.join('docs', 'layout.json')


def _parents_first(rows):
    """Order rows so a parent is always written before its children.

    The loader saves in file order, and a section whose parent has not been
    written yet is a foreign key to a row that does not exist. The database
    happens to hand these back in an order that mostly works, which is exactly
    the kind of luck that fails on somebody else's machine.
    """
    by_pk = {r.pk: r for r in rows}
    out, seen = [], set()

    def emit(row):
        if row.pk in seen:
            return
        seen.add(row.pk)
        parent_pk = getattr(row, 'parent_id', None)
        if parent_pk and parent_pk in by_pk:
            emit(by_pk[parent_pk])
        out.append(row)

    for row in rows:
        emit(row)
    return out


class Command(BaseCommand):
    help = 'Write the homepage layout and the navigation to a fixture.'

    def add_arguments(self, parser):
        parser.add_argument('--out', default=DEFAULT_OUT,
                            help=f'Where to write it. Default {DEFAULT_OUT}')
        # The homepage is identified by the path it is bound to, not by its
        # slug: its slug is the unremarkable 'page' and the thing that makes it
        # the homepage is bound_path='/'.
        parser.add_argument('--path', default='/',
                            help='bound_path of the page to export. Default /')

    def handle(self, *args, **options):
        path = options['path']
        page = Page.objects.filter(bound_path=path, draft_of__isnull=True).first()
        if page is None:
            self.stderr.write(self.style.ERROR(f'No page bound to {path!r}.'))
            return

        sections = _parents_first(list(Section.objects.filter(page=page)))
        items = list(SectionItem.objects.filter(section__in=sections))
        nav = _parents_first(list(NavItem.objects.all()))
        branding = list(SiteBranding.objects.all())

        # Anything pointing outside the layout is cut loose. A row referring to
        # user 1249, or to asset 87 in this machine's media library, is a foreign
        # key to something the server has never heard of, and SQLite fails the
        # whole load at commit time rather than the offending row. Nulled here,
        # so the fixture is safe by construction instead of relying on the far
        # side to cope.
        dropped = []
        for row in [page]:
            for field in ('updated_by_id', 'draft_started_by_id'):
                if getattr(row, field, None):
                    setattr(row, field, None)
        for row in sections:
            if getattr(row, 'image_asset_id', None):
                dropped.append(f'{row.type} block image')
                row.image_asset_id = None
            row.origin_id = None
        for row in items:
            if getattr(row, 'image_asset_id', None):
                dropped.append(f'item image in {row.section_id}')
                row.image_asset_id = None
            if getattr(row, 'person_id', None):
                dropped.append(f'person on item {row.pk}')
                row.person_id = None

        rows = [page] + sections + items + nav + branding
        data = serializers.serialize('json', rows, indent=1)

        out = options['out']
        parent = os.path.dirname(out)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(out, 'w', encoding='utf-8') as fh:
            fh.write(data)

        self.stdout.write(self.style.SUCCESS(f'  {out}'))
        self.stdout.write(f'  page      {page.title} ({page.bound_path})')
        self.stdout.write(f'  sections  {len(sections)}')
        self.stdout.write(f'  items     {len(items)}')
        self.stdout.write(f'  nav       {len(nav)}')
        self.stdout.write(f'  branding  {len(branding)}')
        if dropped:
            self.stdout.write(self.style.WARNING(
                f'  dropped   {len(dropped)} reference(s) to this machine\'s own '
                f'media or people, which the server does not have:'))
            for what in dropped[:6]:
                self.stdout.write(f'              {what}')
        self.stdout.write('')
        self.stdout.write('Copy it to the server and run: manage.py import_layout')
