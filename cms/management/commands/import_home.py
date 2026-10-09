# cms/management/commands/import_home.py
"""Convert the hardcoded homepage into builder data.

This is the step that was missing: binding a route to a Page produced an empty
section list next to a full page, so "Manage content" showed nothing of what was
actually on screen. This reads the homepage as it exists today and writes the
same structure as Page -> Section -> SectionItem, so opening the editor shows
the real page with every block editable.

The output is deliberately an exact match for the current layout:

    greeting                      full width
    link tiles (6)                full width
    [ agenda       | news list ]  55 / 45, equal height
    [ project reel | event list ] 55 / 45, equal height
    booth strip                   full width
    birthdays                     full width
    community                     full width

The workspace row leads because the first thing somebody wants off the homepage
is what they are due at today; news is what they read once that is answered.
Both rows stretch their columns, so the four cards read as one grid rather than
as two pairs that happen to sit above each other.

    manage.py import_home          # create (refuses to clobber)
    manage.py import_home --force  # rebuild from scratch
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from cms.models import Layout, Page, Section, SectionItem, SectionType

HOME_PATH = '/'

#: The six tiles exactly as the template hardcodes them, in order. `meta` is the
#: small grey subtitle; `icon` names come from the CMS icon registry.
TILES = [
    ('MenaME', 'Self-service', 'grid',
     'https://hrms.menaitechsystems.com/AJGroup/application/hrms/mename/index.php'),
    ('Time Sheets', 'Log hours', 'calendar', '#'),
    ('Servicedesk', 'IT & facilities', 'help', 'https://ajigsdesk/'),
    ('Phone Extensions', 'Directory', 'phone', '/people/extensions/'),
    ('Company Hierarchy', 'Org chart', 'building', '/people/org/'),
    ('Employee Directory', 'Find anyone', 'users', '/people/'),
]


class Command(BaseCommand):
    help = 'Rebuild the homepage as editable builder blocks.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
                            help='Delete the existing home blocks and rebuild.')

    @transaction.atomic
    def handle(self, *args, **opts):
        page = Page.for_path(HOME_PATH)
        if page is None:
            page = Page.objects.create(
                title='Home', slug='home', bound_path=HOME_PATH,
                is_published=True,
                subtitle='The Hub landing page, built from blocks.')
            self.stdout.write('Created the Home page and bound it to /.')
        else:
            existing = page.sections.count()
            if existing and not opts['force']:
                self.stdout.write(self.style.WARNING(
                    f'Home already has {existing} blocks. Re-run with --force '
                    f'to rebuild, which will discard any edits made since.'))
                return
            page.sections.all().delete()
            page.bound_path = HOME_PATH
            page.is_published = True
            page.save()

        n = 0

        # 1 — greeting
        Section.objects.create(page=page, type=SectionType.GREETING, order=10,
                               subtitle='AJi Jordan Office')
        n += 1

        # 2 — the tile strip
        tiles = Section.objects.create(
            page=page, type=SectionType.LINK_TILES, order=20,
            config={'columns': 6})
        for i, (title, meta, icon, url) in enumerate(TILES, start=1):
            SectionItem.objects.create(section=tiles, order=i * 10, title=title,
                                       meta=meta, icon=icon, url=url)
        n += 1

        # 3 — your day beside what is new to read
        row1 = Section.objects.create(page=page, type=SectionType.ROW, order=30,
                                      layout=Layout.WIDE_L, equal_height=True,
                                      title='Workspace and news')
        Section.objects.create(page=page, parent=row1, column=0, order=10,
                               type=SectionType.AGENDA,
                               title='Your workspace',
                               subtitle="Today's agenda",
                               # Five, not three: the card is as tall as the news
                               # card beside it, and three single-line entries
                               # left the bottom third of it empty.
                               config={'limit': 5, 'link_url': '/calendar/',
                                       'link_label': 'Calendar'})
        Section.objects.create(page=page, parent=row1, column=1, order=10,
                               type=SectionType.NEWS_LIST,
                               title='Latest from AJi News',
                               config={'limit': 3, 'link_url': '/news/',
                                       'link_label': 'All news'})
        n += 3

        # 4 — the project carousel beside the events card
        row2 = Section.objects.create(page=page, type=SectionType.ROW, order=40,
                                      layout=Layout.WIDE_L, equal_height=True,
                                      title='Projects and events')
        Section.objects.create(page=page, parent=row2, column=0, order=10,
                               type=SectionType.PROJECT_REEL,
                               title='New projects at AJi',
                               config={'limit': 12, 'link_url': '/portfolio/',
                                       'link_label': 'View portfolio'})
        Section.objects.create(page=page, parent=row2, column=1, order=10,
                               type=SectionType.EVENT_LIST,
                               title='Official events',
                               config={'limit': 3, 'link_url': '/calendar/',
                                       'link_label': 'Calendar'})
        n += 3

        # 5 — the one band on the page that is a picture, placed where the four
        # bordered cards above it have run their course.
        Section.objects.create(page=page, type=SectionType.BOOTH_REEL, order=45,
                               title='From AJi Booth',
                               config={'limit': 10, 'link_url': '/booth/',
                                       'link_label': 'All albums'})
        n += 1

        # 6 and 7 — full-width closers
        Section.objects.create(page=page, type=SectionType.BIRTHDAYS, order=50,
                               title='Birthdays today', config={'limit': 3})
        Section.objects.create(page=page, type=SectionType.COMMUNITY, order=60,
                               title='From your communities',
                               config={'limit': 6, 'link_url': '/exchange/',
                                       'link_label': 'AJi Exchange'})
        n += 2

        self.stdout.write(self.style.SUCCESS(
            f'Home rebuilt as {n} blocks ({page.sections.count()} rows including '
            f'those inside columns). Open / and switch on Edit page.'))
