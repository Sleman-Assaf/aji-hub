# cms/management/commands/migrate_content.py
"""Move hardcoded content out of views and templates into editable records.

Phase 2 of the audit. The content here was either a literal Python list in a view
or literal markup in a template, so none of it could be changed without a deploy —
and in one case the statistics existed twice, in a view and as fallback markup, free
to disagree with each other.

Every string is copied verbatim from where it already lived. Nothing is reworded:
the point is to make the same content editable, not to rewrite it.

    manage.py migrate_content
    manage.py migrate_content --force   # rebuild, discarding edits made since
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from cms.models import Layout, Page, Section, SectionItem, SectionType

#: Copied from portal/views.py where they were literals. The icons are chosen per
#: row rather than one shape repeated for every cell, which the markup did.
STATISTICS = [
    ('60+', 'Years', 'clock'),
    ('1,000+', 'Professionals', 'users'),
    ('2,000+', 'Projects', 'briefcase'),
    ('20+', 'Countries', 'globe'),
]

KEY_VALUES = [
    ('Innovation', 'sparkles'),
    ('Integration', 'grid'),
    ('Integrity', 'shield'),
    ('Inclusivity', 'heart'),
]

EXPERTISE = [
    ('Urban Environments', 'building'),
    ('Wellness & Healthcare', 'heart'),
    ('Eco-Futurism', 'globe'),
    ('Utilities & Environment', 'settings'),
    ('Mobility & Transportation', 'map-pin'),
    ('Advisory & Solutions', 'compass'),
    ('Assets & Operations', 'briefcase'),
]

#: The two paragraphs from the "Regional engineering pioneers" card, verbatim.
ABOUT_BODY = (
    "As regional engineering pioneers, AJi Group builds on a rich legacy of "
    "forward-looking projects across the MENA region spanning over 60 years — one "
    "of the region's leading firms in architecture, engineering and total "
    "infrastructure consultancy.\n\n"
    "With over 1,000 professionals across Saudi Arabia, the Emirates, Oman, "
    "Jordan, Iraq, Kuwait and Yemen, and 2,000+ successful projects in 20+ "
    "countries, AJi's portfolio spans the Middle East and reaches from North "
    "America to Central Asia."
)


class Command(BaseCommand):
    help = 'Convert hardcoded lists and card markup into editable CMS records.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
                            help='Rebuild even if the blocks already exist.')

    @transaction.atomic
    def handle(self, *args, **opts):
        page = Page.for_path('/onboarding/')
        if page is None:
            self.stdout.write(self.style.ERROR('No page is bound to /onboarding/.'))
            return

        made = self.migrate_about(page, opts['force'])
        made += self.migrate_expertise(page, opts['force'])

        if made:
            self.stdout.write(self.style.SUCCESS(
                f'Migrated {made} block(s). The statistics, values and disciplines '
                f'are records now — edit them on the page.'))

    # ── About AJi: prose + statistics beside the value cards ─────────────
    def migrate_about(self, page, force):
        existing = page.sections.filter(type=SectionType.TEXT_STATS)
        if existing.exists() and not force:
            self.stdout.write(self.style.WARNING(
                'About AJi already migrated — use --force to rebuild.'))
            return 0

        # Replace the locked feature that rendered this section as markup.
        old = [s for s in page.sections.filter(type=SectionType.FEATURE)
               if (s.config or {}).get('widget') == 'ob_about']
        order = old[0].order if old else 30
        title = old[0].title if old else 'About AJi'
        for s in old:
            s.delete()
        page.sections.filter(type__in=[SectionType.TEXT_STATS,
                                       SectionType.VALUE_CARDS]).delete()
        page.sections.filter(type=SectionType.ROW, title='About AJi').delete()

        # The original was a two-column grid at 1.4fr / 1fr, which is the 55/45
        # split rounded to the nearest option in the layout picker.
        row = Section.objects.create(page=page, type=SectionType.ROW, order=order,
                                     layout=Layout.WIDE_L, title=title)

        left = Section.objects.create(
            page=page, parent=row, column=0, order=10,
            type=SectionType.TEXT_STATS,
            title='Regional engineering pioneers', icon='globe',
            body=ABOUT_BODY)
        for i, (value, label, icon) in enumerate(STATISTICS, start=1):
            SectionItem.objects.create(section=left, order=i * 10,
                                       title=value, meta=label, icon=icon)

        right = Section.objects.create(
            page=page, parent=row, column=1, order=10,
            type=SectionType.VALUE_CARDS,
            title='Key values', icon='heart', config={'columns': 2})
        for i, (value, icon) in enumerate(KEY_VALUES, start=1):
            SectionItem.objects.create(section=right, order=i * 10,
                                       title=value, icon=icon)

        self.stdout.write(
            f'  About AJi -> a 55/45 row: prose with {len(STATISTICS)} statistics '
            f'beside {len(KEY_VALUES)} value cards')
        return 3

    # ── Our Expertise: seven disciplines as tiles ────────────────────────
    def migrate_expertise(self, page, force):
        existing = page.sections.filter(type=SectionType.ICON_TILES)
        if existing.exists() and not force:
            self.stdout.write(self.style.WARNING(
                'Expertise already migrated — use --force to rebuild.'))
            return 0

        old = [s for s in page.sections.filter(type=SectionType.FEATURE)
               if (s.config or {}).get('widget') == 'ob_expertise']
        order = old[0].order if old else 40
        title = old[0].title if old else 'Our Expertise'
        for s in old:
            s.delete()
        existing.delete()

        tiles = Section.objects.create(
            page=page, type=SectionType.ICON_TILES, order=order,
            title=title, config={'columns': 4})
        for i, (area, icon) in enumerate(EXPERTISE, start=1):
            SectionItem.objects.create(section=tiles, order=i * 10,
                                       title=area, icon=icon)

        self.stdout.write(f'  Our Expertise -> {len(EXPERTISE)} editable tiles')
        return 1
