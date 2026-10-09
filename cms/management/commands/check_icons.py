"""Report any icon name on the Hub that no longer draws anything.

Icons are referenced by name from templates, from content rows and from the icon
library, and a name that resolves to nothing renders as empty space rather than an
error — so a broken one is invisible until somebody notices a gap in a button.
This is the check that makes it visible.

    python manage.py check_icons
    python manage.py check_icons --verbose
"""
import re

from django.core.management.base import BaseCommand

from cms import phosphor
from cms.icon_models import BUILTIN_ICONS, ICON_FIELDS, shipped_shape

#: {% icon "name" ... %} and {% cms_icon "name" ... %} in a template.
TAG = re.compile(r'{%\s*(?:cms_)?icon\s+["\']([a-z0-9-]+)["\']')
#: ico('name') / svgIcon('name', …) in the interface's own JavaScript.
JS = re.compile(r"""\b(?:ico|svgIcon)\(\s*['"]([a-z0-9-]+)['"]""")


class Command(BaseCommand):
    help = 'Check that every icon name in use resolves to a shape.'

    def add_arguments(self, parser):
        parser.add_argument('--verbose', action='store_true',
                            help='List every name and where it resolves from.')

    def handle(self, *args, **options):
        import pathlib
        from django.conf import settings

        found = {}          # name -> set of places it is used

        for root in settings.TEMPLATES[0]['DIRS']:
            for f in pathlib.Path(root).rglob('*.html'):
                text = f.read_text(encoding='utf-8')
                for pattern in (TAG, JS):
                    for name in pattern.findall(text):
                        found.setdefault(name, set()).add(f.name)

        from django.apps import apps
        for app_label, model_name, field in ICON_FIELDS:
            try:
                model = apps.get_model(app_label, model_name)
                manager = getattr(model, 'all_objects', model._default_manager)
                names = (manager.exclude(**{field: ''})
                         .values_list(field, flat=True).distinct())
            except Exception as exc:                       # pragma: no cover
                self.stderr.write(f'  could not read {model_name}.{field}: {exc}')
                continue
            for name in names:
                if name:
                    found.setdefault(name, set()).add(f'{model_name}.{field}')

        from cms.models import Icon
        for name in Icon.objects.values_list('name', flat=True):
            found.setdefault(name, set()).add('icon library')

        broken, from_set, from_legacy, from_row = [], [], [], []
        rows = set(Icon.objects.exclude(body='').values_list('name', flat=True))
        for name in sorted(found):
            if name in rows:
                from_row.append(name)
            elif phosphor.resolve(name):
                from_set.append(name)
            elif name in BUILTIN_ICONS:
                from_legacy.append(name)
            elif shipped_shape(name)[0]:
                from_legacy.append(name)
            else:
                broken.append(name)

        self.stdout.write(f'{len(found)} icon names in use')
        self.stdout.write(f'  {len(from_set):4} drawn from '
                          f'{phosphor.version()["set"]} {phosphor.version()["version"]}')
        self.stdout.write(f'  {len(from_row):4} drawn or uploaded here')
        self.stdout.write(f'  {len(from_legacy):4} still on an old hand-drawn shape')

        if options['verbose'] and from_legacy:
            for name in from_legacy:
                self.stdout.write(f'      {name:24} {", ".join(sorted(found[name]))[:60]}')

        if broken:
            self.stdout.write(self.style.ERROR(
                f'  {len(broken)} name(s) draw nothing at all:'))
            for name in broken:
                self.stdout.write(self.style.ERROR(
                    f'      {name:24} used in {", ".join(sorted(found[name]))[:70]}'))
            return

        self.stdout.write(self.style.SUCCESS('  every name resolves to a shape'))
