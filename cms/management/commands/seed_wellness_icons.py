# cms/management/commands/seed_wellness_icons.py
"""Add the health and activity icons the wellness programme needs.

The icon library was 103 generic interface icons — arrows, folders, chevrons —
with nothing for a gym, a walk or a health screening. Every wellness card
therefore drew the same heart, which told a reader nothing about which one they
were looking at.

Drawn to the same rules as the rest of the set: a 24x24 box, stroked rather than
filled, no colour of its own so it takes the colour of whatever it sits in.
Re-runnable: an icon that already exists is left alone unless --force is given,
so this never overwrites one somebody has since edited.
"""
from django.core.management.base import BaseCommand

from cms.icon_models import Icon, IconCategory

#: name → (label, inner SVG for a 24×24 stroked viewBox)
ICONS = {
    'weight-scale': (
        'Weight scale',
        '<rect x="3" y="6" width="18" height="14" rx="2"/>'
        '<path d="M12 10v3"/>'
        '<path d="M8.5 13a3.5 3.5 0 0 1 7 0"/>'
        '<path d="M3 6l2-2h14l2 2"/>'
    ),
    'dumbbell': (
        'Dumbbell',
        '<path d="M6.5 8.5v7"/><path d="M3.5 10v4"/>'
        '<path d="M17.5 8.5v7"/><path d="M20.5 10v4"/>'
        '<path d="M6.5 12h11"/>'
    ),
    'brain': (
        'Mental health',
        '<path d="M12 5.5a3 3 0 0 0-5.6 1.4A2.8 2.8 0 0 0 4 9.6c0 1 .5 1.9 1.3 2.4'
        'A2.9 2.9 0 0 0 5 13.4c0 1.5 1.1 2.7 2.6 2.9a2.7 2.7 0 0 0 4.4 1.6z"/>'
        '<path d="M12 5.5a3 3 0 0 1 5.6 1.4A2.8 2.8 0 0 1 20 9.6c0 1-.5 1.9-1.3 2.4'
        'a2.9 2.9 0 0 1 .3 1.4c0 1.5-1.1 2.7-2.6 2.9a2.7 2.7 0 0 1-4.4 1.6z"/>'
        '<path d="M12 5.5v13.5"/>'
    ),
    'apple-nutrition': (
        'Nutrition',
        '<path d="M12 8.5c-1-1.6-3-2.3-4.7-1.4C5.4 8.1 4.7 10.6 5.4 13c.8 2.7 3 5.5 '
        '6.6 6.9 3.6-1.4 5.8-4.2 6.6-6.9.7-2.4 0-4.9-1.9-5.9-1.7-.9-3.7-.2-4.7 1.4"/>'
        '<path d="M12 8.5V5.5"/>'
        '<path d="M12 5.5c1.6 0 2.9-1.1 3.1-2.5-1.6-.2-3 .8-3.1 2.5z"/>'
    ),
    'football': (
        'Football',
        '<circle cx="12" cy="12" r="8.5"/>'
        '<path d="M12 7.2l2.9 2.1-1.1 3.4h-3.6l-1.1-3.4z"/>'
        '<path d="M12 3.5v3.7"/><path d="M20 9.6l-5.1-.3"/>'
        '<path d="M17 19.4l-3.2-6.7"/><path d="M7 19.4l3.2-6.7"/>'
        '<path d="M4 9.6l5.1-.3"/>'
    ),
    'awareness-ribbon': (
        'Awareness ribbon',
        '<path d="M12 3.5c-2.2 2.4-3.4 4.5-3.4 6.4 0 1.6.8 2.9 1.9 4"/>'
        '<path d="M12 3.5c2.2 2.4 3.4 4.5 3.4 6.4 0 1.6-.8 2.9-1.9 4"/>'
        '<path d="M10.5 13.9L7 20.5l3.4-1.4 1.6 1.4 1.6-1.4 3.4 1.4-3.5-6.6"/>'
    ),
    'stethoscope': (
        'Health screening',
        '<path d="M6 3.5v4.5a3 3 0 0 0 6 0V3.5"/>'
        '<path d="M6 3.5H4.5"/><path d="M12 3.5h1.5"/>'
        '<path d="M9 11v2.5a5 5 0 0 0 5 5h1"/>'
        '<circle cx="17.5" cy="18.5" r="2.5"/>'
    ),
    'footsteps': (
        'Walking',
        '<path d="M8 4.5c1.3 0 2.2 1.1 2.1 2.6l-.4 3.4c-.1 1.1-.9 1.8-1.9 1.8'
        's-1.8-.7-1.9-1.8L5.6 7c-.2-1.4.9-2.5 2.4-2.5z"/>'
        '<path d="M6.1 14.6h3.5l-.3 2.4c-.1.9-.7 1.5-1.5 1.5s-1.4-.6-1.5-1.5z"/>'
        '<path d="M16 8.5c1.3 0 2.2 1.1 2.1 2.6l-.4 3.4c-.1 1.1-.9 1.8-1.9 1.8'
        's-1.8-.7-1.9-1.8L13.6 11c-.2-1.4.9-2.5 2.4-2.5z"/>'
        '<path d="M14.1 18.6h3.5l-.3 1.4c-.1.7-.7 1.1-1.5 1.1s-1.4-.4-1.5-1.1z"/>'
    ),
    'running-shoe': (
        'Steps challenge',
        '<path d="M3 15.5h14.5a3.5 3.5 0 0 0 3.5-3.5c0-1-.6-1.8-1.6-2.1l-4.6-1.4'
        '-2.3-2.6-1.5 1.1 1.3 1.6-2.2.9-2-2.2-1.4 1.2 1.6 1.9-3.1 1.3z"/>'
        '<path d="M3 15.5v2a2 2 0 0 0 2 2h12a4 4 0 0 0 4-4"/>'
    ),
    'ergonomic-chair': (
        'Ergonomics',
        '<path d="M8 4.5h6.5a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2H8z"/>'
        '<path d="M7 13.5h9"/><path d="M11.5 13.5v3.5"/>'
        '<path d="M8 20.5l3.5-3.5 3.5 3.5"/>'
        '<path d="M17.5 8.5h2.5"/>'
    ),
}

#: Which wellness programme gets which icon. Matched on a distinctive word so a
#: renamed programme still lands on the right one.
DEFAULTS = [
    ('lose to win',   'weight-scale'),
    ('gym',           'dumbbell'),
    ('mental health', 'brain'),
    ('nutrition',     'apple-nutrition'),
    ('football',      'football'),
    ('breast cancer', 'awareness-ribbon'),
    ('medical',       'stethoscope'),
    ('walk',          'footsteps'),
    ('steps',         'running-shoe'),
    ('ergonomic',     'ergonomic-chair'),
]


class Command(BaseCommand):
    help = 'Add the health and activity icons, and give each wellness card one.'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true',
                            help='Overwrite the shape of an icon that already exists.')
        parser.add_argument('--reassign', action='store_true',
                            help='Set the icon on every wellness card, even one '
                                 'that already has a different icon chosen.')

    def handle(self, *args, **options):
        made = updated = 0
        for name, (label, body) in ICONS.items():
            icon, created = Icon.objects.get_or_create(
                name=name,
                defaults={'label': label, 'body': body,
                          'category': IconCategory.CONTENT, 'is_builtin': True,
                          'stroke': 1.7},
            )
            if created:
                made += 1
            elif options['force']:
                icon.label, icon.body = label, body
                icon.stroke = 1.7
                icon.save(update_fields=['label', 'body', 'stroke'])
                updated += 1
        self.stdout.write(f'Icons: {made} added, {updated} rewritten, '
                          f'{len(ICONS) - made - updated} already present')

        # Icon.save() drops the cache itself, but a get_or_create that changed
        # nothing does not, so clear it once here.
        from django.core.cache import cache
        from cms.icon_models import CACHE_KEY
        cache.delete(CACHE_KEY)

        from portal.models import WellnessProgram
        touched = 0
        for prog in WellnessProgram.objects.all():
            if prog.icon and not options['reassign']:
                continue
            lowered = prog.name.lower()
            for needle, icon_name in DEFAULTS:
                if needle in lowered:
                    prog.icon = icon_name
                    prog.save(update_fields=['icon'])
                    touched += 1
                    self.stdout.write(f'  {prog.name} -> {icon_name}')
                    break
        self.stdout.write(f'Wellness cards given an icon: {touched}')
