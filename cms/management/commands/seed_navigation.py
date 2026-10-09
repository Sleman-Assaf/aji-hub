"""Turn the hardcoded navigation into records, exactly as it was.

NAV_ITEMS lived as a JavaScript array in base.html, so every label, link, icon
and dropdown grouping needed a template edit. This copies that array into NavItem
rows one for one, so the menu looks and behaves the same the moment it starts
being read from the database instead.

Safe to run twice: it will not add a second copy of anything.

    python manage.py seed_navigation
    python manage.py seed_navigation --dry-run
"""
from django.core.management.base import BaseCommand
from django.db import transaction

#: Exactly what base.html had, in order. Icons are the names the dropdowns used.
NAV = [
    ('Home', '/', '', []),
    ('People & Culture', '', '', [
        ('People & Culture', '/people-culture/', 'heart'),
        ('Onboarding Module', '/onboarding/', 'book'),
        ('AJi Booth', '/booth/', 'camera'),
    ]),
    ('Directory', '', '', [
        ('Employee Directory', '/people/', 'idcard'),
        ('AJi Skills', '/people/skills/', 'spark'),
        ('Phone Extensions', '/people/extensions/', 'phone'),
        ('Org Hierarchy', '/people/org/', 'org'),
        ('QR Codes', '/people/qr/', 'chat'),
    ]),
    ('AJi Exchange', '/exchange/', '', []),
    ('SOPs & Manuals', '/policies/', '', []),
    ('Brand Files', '/brand/', '', []),
]


class Command(BaseCommand):
    help = 'Create the navigation records from the layout base.html had.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Say what would change and write nothing.')

    def handle(self, *args, **options):
        from cms.models import NavItem, SiteBranding

        dry = options['dry_run']
        made, skipped = [], []

        with transaction.atomic():
            for i, (label, url, icon, kids) in enumerate(NAV):
                top = NavItem.objects.filter(label=label, parent__isnull=True).first()
                if top is None:
                    made.append(label)
                    if not dry:
                        top = NavItem.objects.create(label=label, url=url, icon=icon,
                                                     order=(i + 1) * 10)
                else:
                    skipped.append(label)

                for j, (klabel, kurl, kicon) in enumerate(kids):
                    if top is None:          # dry run, parent was never created
                        made.append(f'  {klabel}')
                        continue
                    if NavItem.objects.filter(label=klabel, parent=top).exists():
                        skipped.append(f'  {klabel}')
                        continue
                    made.append(f'  {klabel}')
                    if not dry:
                        NavItem.objects.create(label=klabel, url=kurl, icon=kicon,
                                               parent=top, order=(j + 1) * 10)

            if not dry:
                SiteBranding.get()           # so the branding screen has a row

            if dry:
                transaction.set_rollback(True)

        verb = 'Would create' if dry else 'Created'
        if made:
            self.stdout.write(f'{verb} {len(made)}:')
            for m in made:
                self.stdout.write(f'  {m}')
        if skipped:
            self.stdout.write(f'Already there: {len(skipped)}')
        if not made:
            self.stdout.write(self.style.SUCCESS('Navigation is already seeded.'))
