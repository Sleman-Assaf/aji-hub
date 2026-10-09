"""Give the handbook chapters their own block on the onboarding page.

The banner and the chapter explorer were one feature, so moving one moved the
other and neither could be removed on its own or have a section put between
them. The template is now two files; this adds the second block right after the
first, so the page reads exactly as it did.

Safe to run twice.

    python manage.py split_handbook_block
    python manage.py split_handbook_block --dry-run
"""
from django.core.management.base import BaseCommand
from django.db import transaction

PATH = '/onboarding/'


class Command(BaseCommand):
    help = 'Add the handbook-chapters block beside the handbook banner.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        from cms.models import Page, Section, SectionType

        dry = options['dry_run']
        page = Page.for_path(PATH)
        if page is None:
            self.stderr.write(f'No page is bound to {PATH}.')
            return

        banner = page.sections.filter(type=SectionType.FEATURE,
                                      config__widget='ob_handbook').first()
        if banner is None:
            self.stderr.write('No handbook banner block on that page — nothing to '
                              'split. Run convert_pages first.')
            return

        if page.sections.filter(type=SectionType.FEATURE,
                                config__widget='ob_handbook_tabs').exists():
            self.stdout.write('Already split — nothing to do.')
            return

        with transaction.atomic():
            # Between the banner and whatever follows it. Sections are spaced ten
            # apart, so there is room without renumbering the page.
            after = (page.sections.filter(parent__isnull=True,
                                          order__gt=banner.order)
                     .order_by('order').first())
            order = banner.order + (5 if (after and after.order - banner.order > 1)
                                    else 1)

            if dry:
                self.stdout.write(f'Would add the chapters block at order {order}, '
                                  f'after the banner at {banner.order}.')
                transaction.set_rollback(True)
                return

            Section.objects.create(
                page=page, type=SectionType.FEATURE, order=order,
                config={'widget': 'ob_handbook_tabs'},
                audience=banner.audience)

        self.stdout.write(self.style.SUCCESS(
            'The handbook chapters are their own block now. Both can be moved, '
            'hidden or removed on their own.'))
