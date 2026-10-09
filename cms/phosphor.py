# cms/phosphor.py
"""The Hub's icon set: Phosphor 2.1.1, regular weight, 1,512 icons.

Before this there were 76 hand-drawn shapes across three dictionaries. Adding one
meant writing SVG path data by hand into a Python file, so in practice nobody
did, and the same handful of icons appeared everywhere. Anyone wanting an icon for
"scaffolding" or "concrete mixer" had no route to one at all.

Three things to know about the shapes themselves:

* Phosphor's regular weight is a *filled* path that traces an outline, not a
  stroked shape. It renders with fill="currentColor" and no stroke. The old
  shapes were the opposite, so the renderer has to tell them apart.
* They are drawn on a 256 grid, not 24.
* Every icon carries categories and search tags from the upstream package, which
  is what lets the picker be browsable and searchable rather than an alphabetical
  wall of 1,512 cells.

Nothing that referred to an old name breaks: LEGACY maps each of the 76 to its
Phosphor equivalent, and any name that resolves to neither still falls back to the
hand-drawn shape in icons.py. See cms/icon_models.render_icon for that order.

Licence: MIT, Copyright (c) 2023 Phosphor Icons. The npm package is a dependency
of the generator that produced cms/data/phosphor.json, not of this project.
"""
import json
import pathlib

DATA_FILE = pathlib.Path(__file__).with_name('data') / 'phosphor.json'

#: Phosphor draws on a 256 grid.
VIEW_BOX = '0 0 256 256'

#: The old names, and what each one is now drawn as. Every name that was in
#: icons.py or icons_ui.py appears here or resolves directly, so no template,
#: database row or piece of content had to be edited.
LEGACY = {
    'mail':          'envelope',
    'award':         'medal',
    'settings':      'gear',
    'help':          'question',
    'alert':         'warning',
    'chart':         'chart-line',
    'grid':          'grid-four',
    'search':        'magnifying-glass',
    'graduation':    'graduation-cap',
    'sparkles':      'sparkle',
    'idcard':        'identification-card',
    'spark':         'sparkle',
    'org':           'tree-structure',
    'menu':          'list',
    'chevron-down':  'caret-down',
    'chevron-left':  'caret-left',
    'chevron-right': 'caret-right',
    'close':         'x',
    'send':          'paper-plane-tilt',
    'check-simple':  'check',
    'whatsapp':      'whatsapp-logo',
    # Two briefcases were two different drawings, and the nav still wants to look
    # unlike a generic one, so the distinction is kept rather than collapsed.
    'briefcase-2':   'briefcase-metal',
    'file-blank':    'file',
    'edit':          'pencil-simple',
    'eye-off':       'eye-slash',
    'grip':          'dots-six-vertical',
    'external':      'arrow-square-out',
    'filter':        'funnel',
    # This one is the "past sessions" button, so it wants the history clock.
    'clock-2':       'clock-counter-clockwise',

    # These five were never in the old set either. The service categories were
    # seeded with the names from the model's own help_text — 'hr', 'finance' and
    # so on — and no shape ever existed for any of them, so those five tiles on
    # My AJi have been rendering a blank space since they were created. Found by
    # `manage.py check_icons`, which is what that command is for.
    'hr':            'users',
    'finance':       'currency-circle-dollar',
    'it':            'desktop-tower',
    'facilities':    'toolbox',
    'approvals':     'seal-check',

    # Icons drawn by hand in the library, from before there was a set to draw
    # from. Each keeps its own shape — a row with a body always wins — so nothing
    # changes on any page. What these entries add is a way back: the library's
    # Reset button needs a shipped shape to return to, and without one it could
    # only refuse. Now each of these can be swapped for the set's version by
    # whoever decides it looks better, one at a time, or left exactly as it is.
    'arrow-left-circle':  'arrow-circle-left',
    'award-ribbon':       'medal',
    'award-star':         'medal',
    'book-spread':        'book-open',
    'calendar-month':     'calendar',
    'calendar-week':      'calendar-dots',
    'clipboard-check':    'clipboard-text',
    'clipboard-tick':     'clipboard-text',
    'clock-face':         'clock',
    'currency-circle':    'currency-circle-dollar',
    'download-arrow':     'download-simple',
    'download-tray':      'tray-arrow-down',
    'dumbbell':           'barbell',
    'ergonomic-chair':    'office-chair',
    'external-link':      'arrow-square-out',
    'file-lines':         'file-text',
    'folder-tab':         'folder',
    'footsteps':          'footprints',
    'heart-round':        'heart',
    'image-hills':        'image',
    'mail-open':          'envelope-open',
    'map-pin-round':      'map-pin',
    'megaphone-small':    'megaphone',
    'megaphone-wide':     'megaphone',
    'people-two':         'users',
    'running-shoe':       'sneaker',
    'search-round':       'magnifying-glass',
    'user-round':         'user-circle',
    'users-group':        'users',
    'weight-scale':       'scales',
    # apple-nutrition, awareness-ribbon, shape-1 and shape-2 are left out: the set
    # has nothing that means the same, and pointing them at an approximation would
    # make Reset quietly replace the icon with the wrong picture.
}

#: Where a category name from the package reads awkwardly in a picker.
CATEGORY_LABELS = {
    'arrows':                   'Arrows',
    'brands':                   'Brands and logos',
    'commerce':                 'Commerce',
    'communications':           'Communication',
    'design':                   'Design',
    'editor':                   'Editing',
    'finances':                 'Finance',
    'games':                    'Games',
    'health & wellness':        'Health and wellness',
    'maps & travel':            'Maps and travel',
    'media':                    'Media',
    'nature':                   'Nature',
    'objects':                  'Objects',
    'office':                   'Office',
    'people':                   'People',
    'system':                   'System',
    'technology & development': 'Technology',
    'weather':                  'Weather',
}

#: Opens the picker on something recognisable. 1,512 icons in alphabetical order
#: is not a starting point; these are the ones the Hub actually uses, in the order
#: an editor is likely to want them.
COMMON = [
    'file-text', 'folder', 'book-open', 'graduation-cap', 'identification-card',
    'users', 'user', 'user-circle', 'buildings', 'briefcase',
    'calendar', 'calendar-check', 'clock', 'bell', 'megaphone',
    'envelope', 'phone', 'chat-circle', 'whatsapp-logo', 'paper-plane-tilt',
    'map-pin', 'globe', 'compass', 'target', 'medal',
    'heart', 'star', 'shield-check', 'lock', 'seal-check',
    'info', 'question', 'warning', 'check-circle', 'x-circle',
    'image', 'video', 'camera', 'chart-line', 'chart-bar',
    'list', 'grid-four', 'magnifying-glass', 'gear', 'sparkle',
    'download-simple', 'upload-simple', 'link', 'arrow-square-out', 'coffee',
]

#: Extra words to search an icon by. Phosphor's own tags are American English and
#: general-audience, so "spanner", "lorry" and "aeroplane" found nothing at all,
#: and neither did most of the vocabulary of an engineering consultancy. An editor
#: should not have to guess the word the icon set happened to choose.
EXTRA_TAGS = {
    # British English
    'trash':            ['bin', 'rubbish', 'dustbin', 'wastebasket'],
    'wrench':           ['spanner'],
    'truck':            ['lorry'],
    'truck-trailer':    ['articulated lorry', 'hgv'],
    'airplane':         ['aeroplane', 'plane', 'flight'],
    'airplane-takeoff': ['aeroplane', 'departure'],
    'airplane-landing': ['aeroplane', 'arrival'],
    'car':              ['motorcar', 'vehicle'],
    'gas-pump':         ['petrol', 'fuel'],
    'elevator':         ['lift'],
    'trash-simple':     ['bin', 'rubbish'],
    'phone':            ['telephone'],
    'cell-signal-full': ['mobile', 'reception'],
    'device-mobile':    ['mobile phone', 'handset'],
    'flashlight':       ['torch'],
    'cookie':           ['biscuit'],
    'football':         ['soccer'],
    'soccer-ball':      ['football'],
    'sneaker':          ['trainer', 'trainers'],
    'eyeglasses':       ['spectacles', 'glasses'],
    'first-aid-kit':    ['first aid'],
    'stairs':           ['staircase', 'stairwell'],

    # What this office actually does
    'chart-line':       ['graph', 'trend'],
    'chart-bar':        ['graph', 'bar chart'],
    'chart-pie':        ['graph', 'pie chart'],
    'hard-hat':         ['helmet', 'ppe', 'site safety', 'construction'],
    'crane':            ['construction', 'site'],
    'crane-tower':      ['construction', 'site', 'tower crane'],
    'bulldozer':        ['earthworks', 'plant', 'excavator', 'digger'],
    'ruler':            ['measure', 'dimension', 'scale'],
    'pencil-ruler':     ['drawing', 'drafting', 'design'],
    'compass-tool':     ['drafting', 'drawing'],
    'blueprint':        ['drawing', 'plan', 'layout'],
    'buildings':        ['office', 'premises', 'estate'],
    'building-office':  ['office', 'premises'],
    'bridge':           ['infrastructure', 'crossing'],
    'road-horizon':     ['highway', 'roads', 'carriageway'],
    'traffic-signal':   ['traffic light', 'junction'],
    'drop':             ['water', 'moisture'],
    'drop-half':        ['water', 'humidity'],
    'lightning':        ['electrical', 'power', 'mep'],
    'plug':             ['electrical', 'power', 'socket'],
    'thermometer':      ['hvac', 'temperature'],
    'fan':              ['hvac', 'ventilation'],
    'wind':             ['hvac', 'ventilation', 'air'],
    'recycle':          ['sustainability', 'environment'],
    'leaf':             ['sustainability', 'environment', 'green'],
    'solar-panel':      ['renewable', 'sustainability', 'pv'],
    'tree':             ['landscape', 'environment'],
    'stamp':            ['approval', 'approved', 'sign off'],
    'seal-check':       ['approval', 'approved', 'certified'],
    'certificate':      ['qualification', 'accreditation'],
    'signature':        ['sign', 'sign off'],
    'gavel':            ['legal', 'contract', 'compliance'],
    'scales':           ['legal', 'balance', 'compliance'],
    'clipboard-text':   ['inspection', 'checklist', 'snag list'],
    'warning':          ['risk', 'hazard', 'caution'],
    'first-aid':        ['safety', 'hse'],
    'users-three':      ['team', 'staff', 'department'],
    'identification-badge': ['staff pass', 'employee', 'id'],
    'currency-circle-dollar': ['finance', 'cost', 'budget'],
    'coins':            ['finance', 'budget', 'cost'],
    'receipt':          ['invoice', 'expense'],
    'invoice':          ['bill', 'payment'],
    'presentation-chart': ['report', 'briefing'],
    'folders':          ['archive', 'records'],
    'file-dashed':      ['draft', 'template'],
}

_CACHE = {}


def _data():
    """The icon set, read once per process."""
    if 'data' not in _CACHE:
        try:
            _CACHE['data'] = json.loads(DATA_FILE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            # A missing or unreadable data file must not take the site down: the
            # hand-drawn shapes still answer for every name the Hub uses.
            _CACHE['data'] = {'icons': {}, 'categories': []}
    return _CACHE['data']


def names():
    """Every Phosphor name."""
    return _data()['icons'].keys()


def resolve(name):
    """The Phosphor name for `name`, following the legacy map, or None.

    Also follows the package's own aliases, so both spellings of a renamed icon
    keep working.
    """
    name = (name or '').strip()
    if not name:
        return None
    icons = _data()['icons']
    if name in icons:
        return name
    mapped = LEGACY.get(name)
    if mapped in icons:
        return mapped
    if 'aliases' not in _CACHE:
        _CACHE['aliases'] = {v['a']: k for k, v in icons.items() if 'a' in v}
    alias = _CACHE['aliases'].get(name)
    return alias if alias in icons else None


def path(name):
    """The shape markup for `name`, or '' when the set has no such icon."""
    target = resolve(name)
    return _data()['icons'][target]['d'] if target else ''


def categories():
    """[(key, label, count)] for the picker, biggest groups first.

    'Common on the Hub' is first and is not a Phosphor category — it is the
    shortlist above, so opening the picker shows something recognisable.
    """
    icons = _data()['icons']
    counts = {}
    for entry in icons.values():
        for cat in entry['c']:
            counts[cat] = counts.get(cat, 0) + 1
    rest = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    common = [n for n in COMMON if n in icons]
    return ([('common', 'Common on the Hub', len(common))]
            + [(key, CATEGORY_LABELS.get(key, key.title()), n) for key, n in rest])


def in_category(key):
    """The names in one category, alphabetical."""
    icons = _data()['icons']
    if key == 'common':
        return [n for n in COMMON if n in icons]
    return sorted(n for n, e in icons.items() if key in e['c'])


def _tags(name, entry):
    """The words one icon can be found by: its own, plus ours. Built once."""
    cache = _CACHE.setdefault('tags', {})
    if name not in cache:
        cache[name] = [t.lower() for t in entry['t']] + [
            t.lower() for t in EXTRA_TAGS.get(name, ())]
    return cache[name]


def search(term, category='', limit=240):
    """Names matching `term`, best first.

    Ranked rather than filtered, because a plain substring match puts
    "chart-line-down" above "chart-line" and buries "user" under thirty
    compounds. An exact name wins, then a name that starts with the term, then a
    tag, then anything containing it.
    """
    icons = _data()['icons']
    raw = (term or '').strip().lower()
    pool = in_category(category) if category else list(icons)
    if not raw:
        # limit=0 means "no cap"; slicing by it here returned an empty list, so
        # every category browsed with no search term looked empty.
        return pool[:limit] if limit else pool

    # "hard hat" and "hard-hat" are the same request, and a two-word search means
    # both words, not the literal string: searching the phrase alone found
    # nothing because no name or tag contains a space.
    term = raw.replace(' ', '-')
    words = [w for w in raw.replace('-', ' ').split() if w]

    scored = []
    for name in pool:
        entry = icons[name]
        tags = _tags(name, entry)
        if name == term:
            rank = 0
        elif name.startswith(term):
            rank = 1
        elif term in tags:
            rank = 2
        elif term in name:
            rank = 3
        elif any(term in t for t in tags):
            rank = 4
        elif any(term in c.lower() for c in entry['c']) or (
                entry.get('a') and term in entry['a']):
            rank = 5
        elif len(words) > 1 and all(
                w in name or any(w in t for t in tags) for w in words):
            rank = 6
        else:
            continue
        # Shorter names first within a rank: "user" before "user-circle-gear".
        scored.append((rank, len(name), name))

    scored.sort()
    hits = [name for _r, _l, name in scored]
    # limit=0 means "all of them", so a caller can report how many matched before
    # deciding how many to draw. Silently capping and saying nothing is how a
    # picker ends up looking like it has no more results.
    return hits[:limit] if limit else hits


def label(name):
    """A human name for a picker cell."""
    return (name or '').replace('-', ' ').capitalize()


def meta(name):
    """Everything known about one icon, for a tooltip or a detail panel."""
    target = resolve(name)
    if not target:
        return None
    entry = _data()['icons'][target]
    return {
        'name': target,
        'label': label(target),
        'categories': [CATEGORY_LABELS.get(c, c) for c in entry['c']],
        'tags': list(entry['t']),
        'alias': entry.get('a', ''),
        'renamed_from': name if name != target else '',
    }


def version():
    """What is installed, for the library screen's footer."""
    d = _data()
    return {
        'set': d.get('set', 'phosphor'),
        'version': d.get('version', ''),
        'weight': d.get('weight', 'regular'),
        'licence': d.get('licence', ''),
        'count': len(d['icons']),
    }
