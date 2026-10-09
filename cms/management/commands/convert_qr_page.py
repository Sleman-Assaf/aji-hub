"""Build /people/qr/ out of blocks, matching what the template used to render.

The page was a hardcoded heading and two cards with emoji standing in for the QR
codes. This creates the heading as an editable block and the codes as a content
panel reading the WhatsAppChannel records, so the page looks the same and every
part of it can now be changed without a code change.

Safe to run twice: it will not add a second copy of anything.

    python manage.py convert_qr_page
    python manage.py convert_qr_page --dry-run
"""
from django.core.management.base import BaseCommand
from django.db import transaction

PATH = '/people/qr/'

#: What the old template said, so the converted page reads identically.
HEAD = {
    'eyebrow': 'Connect',
    'title': 'QR Codes',
    'subtitle': "Scan to join AJi's official WhatsApp channels.",
}


class Command(BaseCommand):
    help = 'Convert the QR codes page to blocks.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Say what would change and write nothing.')

    def handle(self, *args, **options):
        from cms.models import Page, Section, SectionType

        dry = options['dry_run']
        page = Page.for_path(PATH)
        if page is None:
            self.stderr.write(f'No page is bound to {PATH}. Bind it first from '
                              f'the edit toolbar.')
            return

        with transaction.atomic():
            made = []

            head = page.sections.filter(type=SectionType.PAGE_HEAD).first()
            if head is None:
                made.append('page heading')
                if not dry:
                    Section.objects.create(
                        page=page, type=SectionType.PAGE_HEAD, order=10,
                        title=HEAD['title'], subtitle=HEAD['subtitle'],
                        config={'eyebrow': HEAD['eyebrow']})

            panel = page.sections.filter(type=SectionType.FEATURE,
                                         config__widget='qr_codes').first()
            if panel is None:
                made.append('QR codes panel')
                if not dry:
                    Section.objects.create(
                        page=page, type=SectionType.FEATURE, order=20,
                        config={'widget': 'qr_codes'})

            if not made:
                self.stdout.write('Already converted — nothing to do.')
                return

            if dry:
                self.stdout.write(f'Would create: {", ".join(made)}')
                # Nothing was written, but roll back anyway so a --dry-run can
                # never leave a half-open transaction behind.
                transaction.set_rollback(True)
                return

        self.stdout.write(self.style.SUCCESS(f'Created: {", ".join(made)}'))
        self.stdout.write(f'Open {PATH}?edit=1 to arrange it.')
