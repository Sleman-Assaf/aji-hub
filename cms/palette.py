# cms/palette.py
"""The component library offered by the editor.

Grouped by what an editor is trying to do, not by where the code came from. The
old grouping was "Sections used on this site" / "Layout" / "Content blocks" /
"Page features": three of those describe the implementation rather than the task,
and the first and third overlapped heavily. Somebody looking for a way to show
four numbers had to already know that "Text with statistics" was the site's own
version and "Statistics" was a weaker copy of the same idea.

Five pairs were near-duplicates. In every case the site's own version had inline
editing, draggable rows and icons, and the generic one was a read-only
approximation. The weak half is RETIRED: still rendered, so no existing page
changes, but no longer offered, so nothing new gets built on it.
"""
from .models import SectionType

#: type → what to use instead. A retired block keeps its template and keeps
#: rendering; it is only withheld from the library, so the editor stops being
#: offered two ways to do one thing.
RETIRED = {
    'quick_links': 'link_tiles',
    'info_cards':  'value_cards',
    'stats':       'text_stats',
    'contacts':    'people',
    'news_feed':   'news_list',
}

#: The library: (key, heading, [(type, label, hint), …]). The order here is the
#: order shown, so the groups an editor reaches for most come first.
LIBRARY = [
    ('text', 'Text and media', [
        (SectionType.PAGE_HEAD, 'Heading',
         'The page title, with an optional line above it and an intro under it.'),
        (SectionType.RICH_TEXT, 'Rich text',
         'A paragraph or several. Bold, links and lists, edited on the page.'),
        (SectionType.HERO, 'Hero',
         'A full-width opening banner with a headline and buttons.'),
        (SectionType.IMAGE, 'Image',
         'One picture, with an optional caption.'),
        (SectionType.GALLERY, 'Gallery',
         'A grid of pictures with captions.'),
        (SectionType.VIDEO, 'Video',
         'An embedded video.'),
        (SectionType.CALLOUT, 'Callout',
         'A short remark that needs to stand out — a deadline, a caveat, a warning.'),
        (SectionType.ANNOUNCEMENT, 'Announcement',
         'A dated notice in its own card.'),
    ]),

    ('layout', 'Layout', [
        (SectionType.ROW, 'Columns',
         'Split the page — 50/50, 33/66, 25/75, thirds or quarters. '
         'Drop any component into a column.'),
    ]),

    ('cards', 'Cards and lists', [
        (SectionType.VALUE_CARDS, 'Cards',
         'A grid of cards, each with an icon, a heading and a line of text.'),
        (SectionType.ICON_TILES, 'Icon tiles',
         'Compact tiles, one icon and one label each.'),
        (SectionType.LINK_TILES, 'Quick links',
         'The bordered strip of shortcut tiles from the homepage.'),
        (SectionType.BUTTONS, 'Button group',
         'A row of buttons. Each has its own label, icon and link.'),
        (SectionType.PEOPLE, 'People',
         'Cards for colleagues. Link one to the directory and the name, role, '
         'photo and contacts follow it.'),
        (SectionType.TEXT_STATS, 'Statistics',
         'A card of prose with a row of big numbers under it.'),
        (SectionType.TABLE, 'Table',
         'Rows and columns of plain data.'),
        (SectionType.FAQ, 'FAQ / accordion',
         'Questions that open to reveal their answers.'),
        (SectionType.TIMELINE, 'Timeline',
         'Dated steps, in order.'),
        (SectionType.DOWNLOADS, 'Downloads',
         'A list of files to download.'),
    ]),

    ('live', 'Live from the Hub', [
        (SectionType.NEWS_LIST, 'News',
         'The latest articles, keeping themselves up to date.'),
        (SectionType.EVENT_LIST, 'Events',
         'Upcoming events with the black date blocks.'),
        (SectionType.AGENDA, 'Today’s agenda',
         "Today's events only, times down the left edge."),
        (SectionType.BIRTHDAYS, 'Birthdays',
         "Today's birthdays with the wish button. Hides itself when there are none."),
        (SectionType.COMMUNITY, 'Communities',
         'Community chips above the latest discussion threads.'),
        (SectionType.PROJECT_REEL, 'Projects',
         'The full-bleed project slider with status pills.'),
        (SectionType.POLICY_LIST, 'Policies',
         'Documents from the policy library.'),
        (SectionType.GREETING, 'Greeting',
         "The dated welcome with the reader's name, clock and office."),
        (SectionType.TABS, 'Tabbed panels',
         'Several panels behind one row of tabs.'),
    ]),

    ('advanced', 'Advanced', [
        (SectionType.CUSTOM_HTML, 'Custom HTML',
         'Raw markup, for something the library does not cover. Stays escaped '
         'until a system administrator approves it.'),
    ]),
]

#: Ready-made layouts. Inserting one creates the row *and* what goes in it, in a
#: single step. The old flow was: add a Columns block, find each empty column, add
#: a component to it, configure that — four or five steps before any content
#: existed. This is one.
PRESETS = [
    {'key': 'two-col', 'label': 'Two columns',
     'hint': 'A 50/50 row with a text block on each side.',
     'layout': '50-50',
     'children': [('rich_text', 0), ('rich_text', 1)]},
    {'key': 'text-image', 'label': 'Text beside an image',
     'hint': 'Words on the left, a picture on the right.',
     'layout': '50-50',
     'children': [('rich_text', 0), ('image', 1)]},
    {'key': 'image-text', 'label': 'Image beside text',
     'hint': 'The picture first. Alternate this with the one above down a page '
             'and it reads as a proper article.',
     'layout': '50-50',
     'children': [('image', 0), ('rich_text', 1)]},
    {'key': 'main-side', 'label': 'Main column with a sidebar',
     'hint': 'A wide column for content and a narrow one beside it.',
     'layout': '66-33',
     'children': [('rich_text', 0), ('link_tiles', 1)]},
    {'key': 'three-col', 'label': 'Three columns',
     'hint': 'Three equal columns, each with a text block.',
     'layout': 'thirds',
     'children': [('rich_text', 0), ('rich_text', 1), ('rich_text', 2)]},

    # Whole openings and sections, not just column splits. These are the shapes
    # a page actually starts with, and building one by hand was six or seven
    # inserts before a word could be typed.
    {'key': 'page-start', 'label': 'Start of a page',
     'hint': 'A heading with an intro, then a paragraph. What almost every new '
             'page begins with.',
     'layout': 'full',
     'children': [('page_head', 0), ('rich_text', 0)]},
    {'key': 'hero-start', 'label': 'Opening banner',
     'hint': 'A full-width hero with buttons, then a row of quick links under it.',
     'layout': 'full',
     'children': [('hero', 0), ('link_tiles', 0)]},
    {'key': 'three-cards', 'label': 'Three cards',
     'hint': 'A heading and three cards with icons — for values, services or '
             'anything that comes in threes.',
     'layout': 'full',
     'children': [('value_cards', 0)]},
    {'key': 'faq-block', 'label': 'Questions and answers',
     'hint': 'A heading with an accordion under it.',
     'layout': 'full',
     'children': [('page_head', 0), ('faq', 0)]},
    {'key': 'team', 'label': 'Meet the team',
     'hint': 'People cards linked to the directory, so names, roles and photos '
             'keep themselves up to date.',
     'layout': 'full',
     'children': [('people', 0)]},
    {'key': 'docs', 'label': 'Documents to download',
     'hint': 'A heading, a short explanation and a file list.',
     'layout': 'full',
     'children': [('page_head', 0), ('downloads', 0)]},
    {'key': 'news-events', 'label': 'News and events side by side',
     'hint': 'The latest articles next to what is coming up. Both keep '
             'themselves current.',
     'layout': '50-50',
     'children': [('news_list', 0), ('event_list', 1)]},
    {'key': 'notice', 'label': 'Notice with detail',
     'hint': 'A callout that stands out, with the explanation under it.',
     'layout': 'full',
     'children': [('callout', 0), ('rich_text', 0)]},
    {'key': 'gallery-story', 'label': 'Gallery with a caption',
     'hint': 'A grid of pictures and the words that go with them.',
     'layout': 'full',
     'children': [('page_head', 0), ('gallery', 0)]},
    {'key': 'stats-row', 'label': 'Numbers that matter',
     'hint': 'A card of prose with big figures under it.',
     'layout': 'full',
     'children': [('text_stats', 0)]},
    {'key': 'four-col', 'label': 'Four columns',
     'hint': 'Four equal columns of icon tiles.',
     'layout': 'quarters',
     'children': [('icon_tiles', 0), ('icon_tiles', 1),
                  ('icon_tiles', 2), ('icon_tiles', 3)]},
]


def _entry(stype, label, hint):
    from .shapes import shape_markup
    value = str(getattr(stype, 'value', stype))
    return {
        'value': value,
        'label': label,
        'hint': hint,
        'shape': shape_markup(value),
        # Everything worth matching on, lowered once here so the client can do a
        # plain substring test. Searching labels alone meant "accordion" never
        # found the FAQ block and "numbers" never found statistics.
        'search': f'{label} {hint} {value}'.lower().replace('_', ' '),
    }


def _feature_entry(key, meta):
    from .shapes import shape_markup
    from .widgets import is_system
    system = is_system(key)
    return {
        'value': str(SectionType.FEATURE),
        'widget': key,
        'label': meta[0],
        'hint': meta[2] + ('' if not system else
                           ' A working part of the Hub, so its inside stays closed.'),
        'system': system,
        'shape': shape_markup(str(SectionType.FEATURE), widget=key),
        'search': f'{meta[0]} {meta[2]} {key}'.lower().replace('_', ' '),
    }


def features():
    """The Hub's content panels: placed, then their wording edited on the page."""
    from .widgets import WIDGETS, is_system
    return [_feature_entry(k, m) for k, m in sorted(WIDGETS.items(),
                                                    key=lambda kv: kv[1][0])
            if not is_system(k)]


def system_features():
    """The four that are applications. Offered last, and labelled as such.

    Kept in the library rather than hidden: a page still has to be able to place
    its calendar or its directory. What changed is that they are no longer sat
    among fifteen panels that all claimed to be locked too.
    """
    from .widgets import WIDGETS, is_system
    return [_feature_entry(k, m) for k, m in sorted(WIDGETS.items(),
                                                    key=lambda kv: kv[1][0])
            if is_system(k)]


def catalogue():
    """The library, keyed by group, in the order it should be shown."""
    out = {key: [_entry(*e) for e in entries] for key, _heading, entries in LIBRARY}
    out['features'] = features()
    out['system'] = system_features()
    return out


def groups():
    """(key, heading) for each group, in the order they should appear."""
    return [(key, heading) for key, heading, _ in LIBRARY] + [
        ('features', 'Panels from around the Hub'),
        ('system', 'Applications — placed, not edited'),
    ]


def presets():
    """The ready-made layouts, each with a wireframe."""
    from .shapes import preset_shape
    return [dict(p, shape=preset_shape(p['key']),
                 search=f"{p['label']} {p['hint']}".lower())
            for p in PRESETS]


def replacement_for(stype):
    """What to use instead of a retired block, or None.

    A page still using one keeps working; this is only so the editor opening it
    is told there is a better version rather than being left to guess why the
    library no longer lists what is on their page.
    """
    target = RETIRED.get(str(stype))
    if not target:
        return None
    for _key, _heading, entries in LIBRARY:
        for value, label, hint in entries:
            if str(getattr(value, 'value', value)) == target:
                return {'value': target, 'label': label, 'hint': hint}
    return None
