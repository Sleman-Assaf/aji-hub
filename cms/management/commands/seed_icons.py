# cms/management/commands/seed_icons.py
"""Load every icon the Hub draws into the editable icon library.

Three sources: the CMS content registry, the navigation set that lived in a
JavaScript object in base.html, and the interface icons that were inline SVG
scattered through the chrome. After this every icon on the site is a row that can
be edited, replaced or swapped for an upload.

Re-runnable. Existing rows keep any customisation — only their metadata is
refreshed — so this never silently reverts an edit.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from cms.icons import ICONS as REGISTRY
from cms.icons_ui import FILLED_ICONS as FILLED, NAV_ICONS as NAV, UI_ICONS as UI
from cms.models import Icon, IconCategory

class Command(BaseCommand):
    help = 'Load every Hub icon into the editable icon library.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true',
                            help='Also discard customisations on built-in icons.')

    @transaction.atomic
    def handle(self, *args, **opts):
        created = updated = kept = 0

        groups = [
            (REGISTRY, IconCategory.CONTENT),
            (NAV, IconCategory.NAV),
            (UI, IconCategory.UI),
        ]

        for i, (source, category) in enumerate(groups):
            for n, (name, body) in enumerate(sorted(source.items())):
                icon, was_created = Icon.objects.get_or_create(
                    name=name,
                    defaults={'category': category, 'is_builtin': True,
                              'order': (i * 1000) + n,
                              'stroke': 2, 'filled': name in FILLED})
                if was_created:
                    # Body stays empty: an empty body means "use the shipped
                    # shape", so the library starts as a faithful mirror of the
                    # code and every row is already customisable.
                    created += 1
                    continue
                if opts['reset'] and icon.body:
                    icon.body = ''
                    icon.save()
                    updated += 1
                    continue
                changed = False
                if icon.category != category and not icon.body:
                    icon.category = category
                    changed = True
                if not icon.is_builtin:
                    icon.is_builtin = True
                    changed = True
                if changed:
                    icon.save()
                    updated += 1
                else:
                    kept += 1

        total = Icon.objects.count()
        custom = Icon.objects.exclude(body='').count()
        self.stdout.write(self.style.SUCCESS(
            f'{total} icons in the library: {created} added, {updated} refreshed, '
            f'{kept} unchanged, {custom} carrying a customisation.'))
