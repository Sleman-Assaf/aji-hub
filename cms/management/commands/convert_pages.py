# cms/management/commands/convert_pages.py
"""Build every converted page out of blocks.

Each page gets a Page bound to its route and a small stack of blocks: a heading
block carrying the title and intro that used to be hardcoded, then the locked
feature that renders the page's working part. From there an editor can add,
reorder and lay out anything around it.

Idempotent by route: a page that already has blocks is left alone unless --force,
so re-running never discards someone's arrangement.

    manage.py convert_pages
    manage.py convert_pages --force
    manage.py convert_pages --only /news/ /policies/
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from cms.models import Page, Section, SectionType

#: route -> (page title, eyebrow, intro, [(feature key, block heading), ...])
#:
#: Pages made of several independent sections get a block each, so a section can
#: be moved, retitled or removed on its own. Wrapping a whole page in one locked
#: feature meant nothing on it could be rearranged, which was the wrong call.
#: Pages that genuinely are one thing — a project grid, a directory search — stay
#: a single feature with an editable heading above it.
#:
#: The eyebrow and intro are the ones the pages already showed, so a converted
#: page reads the same on first load; they are editable from that point.
PAGES = [
    ('/news/', 'AJi News', 'Company',
     'Company news, HR activities, recognitions and announcements from across the group.',
     [('news_index', '')]),
    ('/calendar/', 'My Calendar', 'Schedule',
     'Meetings, holidays and travel.',
     [('calendar', '')]),
    ('/people-culture/', 'People & Culture', 'HR',
     'The main place for all HR policies & guidelines.',
     [('pc_links', ''), ('pc_hr', 'HR Information'),
      ('pc_work', 'Work at AJi'), ('pc_explore', 'Explore People & Culture')]),
    ('/portfolio/', 'Projects', 'Portfolio',
     "Explore AJi Group's project portfolio across all business divisions. Filter by type, division, and status.",
     [('portfolio', '')]),
    ('/policies/', 'SOPs & Manuals', 'Governance',
     'Your central, trusted source for all company policies, guidelines, SOPs and compliance documents.',
     [('policies', '')]),
    ('/brand/', 'Brand Files', 'Corporate Identity',
     'Official AJi logos, document templates and brand guidelines — keep every deliverable on-brand across the group.',
     [('brand_templates', 'Templates'),
      ('brand_colours', 'Colour Palette'), ('brand_assets', 'Brand Assets')]),
    ('/onboarding/', 'Welcome to AJi Group', 'Onboarding Module',
     'Words from our CEO, who we are, and the complete employee handbook — everything a new joiner needs for a confident start.',
     [('ob_ceo', ''), ('ob_handbook', '')]),
    ('/booth/', 'AJi Booth', 'People & Culture',
     'Photo & video albums from AJi life — events, celebrations, and everything in between.',
     [('booth', '')]),
    ('/exchange/', 'AJi Exchange', 'Knowledge',
     'Communities where AJi people ask questions, share guides, documents, research, links and videos, and learn across disciplines.',
     [('exchange', '')]),
    ('/my-aji/', 'Services', 'My AJi',
     'Everything available to your role, in one place.',
     [('my_aji', '')]),
    ('/people/', 'Employee Directory', 'Directory',
     'Search and find any AJi Group employee across all entities and departments.',
     [('directory', '')]),
    ('/cafeteria/', 'Cafeteria', 'My AJi / Workplace & Facilities / Cafeteria',
     'Order drinks & snacks — delivered to your desk or meeting room.',
     [('cafeteria', '')], {'eyebrow_url': '/my-aji/'}),
    ('/training/', 'Training requests', 'Training & Development',
     "Everything you've asked for, and anything waiting on you.",
     [('training', '')]),
]


class Command(BaseCommand):
    help = 'Build the converted pages out of blocks.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
                            help='Rebuild pages that already have blocks.')
        parser.add_argument('--only', nargs='*', default=None,
                            help='Limit to these routes.')

    @transaction.atomic
    def handle(self, *args, **opts):
        built, kept = [], []

        for entry in PAGES:
            path, title, eyebrow, intro, features = entry[:5]
            extra_cfg = entry[5] if len(entry) > 5 else {}
            if opts['only'] and path not in opts['only']:
                continue

            page = Page.for_path(path)
            if page is None:
                slug = path.strip('/').replace('/', '-') or 'home'
                # A slug can collide with an existing page, so make it unique
                # rather than failing the whole conversion.
                base, n = slug, 2
                while Page.objects.filter(slug=slug).exists():
                    slug = f'{base}-{n}'
                    n += 1
                page = Page.objects.create(title=title, slug=slug, bound_path=path,
                                           is_published=True, eyebrow=eyebrow[:80],
                                           subtitle=intro[:400])
            else:
                if page.sections.exists() and not opts['force']:
                    kept.append(path)
                    continue
                page.sections.all().delete()
                page.is_published = True
                page.save()

            # The page heading, previously hardcoded in the template. The feature
            # partials no longer render one, so this is the only title on the page.
            Section.objects.create(
                page=page, type=SectionType.PAGE_HEAD, order=10,
                title=title, subtitle=intro,
                config={'eyebrow': eyebrow, **extra_cfg})

            # One block per section of the page. Each carries its own editable
            # heading; only the markup inside it is locked.
            for i, (widget, heading) in enumerate(features, start=1):
                Section.objects.create(
                    page=page, type=SectionType.FEATURE, order=10 + i * 10,
                    title=heading, config={'widget': widget})

            built.append(path)

        if built:
            self.stdout.write(self.style.SUCCESS(
                f'Built {len(built)} pages from blocks:'))
            for path in built:
                self.stdout.write(f'  {path}')
        if kept:
            self.stdout.write(self.style.WARNING(
                f'Left {len(kept)} page(s) alone because they already have blocks '
                f'(use --force to rebuild): {", ".join(kept)}'))
