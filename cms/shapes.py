# cms/shapes.py
"""Wireframe previews for the block palette.

Choosing a block from a list of names means knowing what "Icon tiles" or
"Text with statistics" looks like before you have ever used one. A small
wireframe removes that: you pick the shape you want on the page.

These are schematics, not renders. Rendering each block for real would need its
data — a news list needs articles, an agenda needs today's events — so a palette
of 49 live previews would fire dozens of queries and show empty states for
anything the database happens not to have. A schematic always shows the shape.

The markup here is authored, never user input, so it is rendered as-is.
Vocabulary: `b` a bar of text, `t` a title bar, `x` a block/box, `i` an icon dot.
"""

#: shape name -> wireframe markup
SHAPES = {
    # ── layout ──────────────────────────────────────────────────────────
    'columns': '<div class="wf-row"><div class="wf-col"><s class="x"></s></div>'
               '<div class="wf-col"><s class="x"></s></div></div>',

    # ── headings and text ───────────────────────────────────────────────
    'page-head': '<s class="b w30 mut"></s><s class="t w70"></s><s class="b w90"></s>',
    'greeting': '<div class="wf-split"><div><s class="b w40 mut"></s>'
                '<s class="t w80"></s></div><s class="x sm"></s></div>',
    'text': '<s class="t w50"></s><s class="b w100"></s><s class="b w100"></s>'
            '<s class="b w60"></s>',
    'quote': '<div class="wf-quote"><s class="b w90"></s><s class="b w70"></s></div>',
    'announce': '<div class="wf-note"><s class="b w40"></s><s class="b w80"></s></div>',
    'hero': '<div class="wf-hero"><s class="t w60 on"></s><s class="b w40 on"></s></div>',

    # ── grids of things ─────────────────────────────────────────────────
    'cards3': '<s class="t w40"></s><div class="wf-grid g3">'
              '<s class="x"></s><s class="x"></s><s class="x"></s></div>',
    'tiles6': '<div class="wf-grid g6 tight">'
              '<s class="x sm"></s><s class="x sm"></s><s class="x sm"></s>'
              '<s class="x sm"></s><s class="x sm"></s><s class="x sm"></s></div>',
    'icon-tiles': '<s class="t w40"></s><div class="wf-grid g4">'
                  '<s class="x ico"></s><s class="x ico"></s>'
                  '<s class="x ico"></s><s class="x ico"></s></div>',
    'values': '<div class="wf-card"><s class="b w40"></s>'
              '<div class="wf-grid g2"><s class="x ico"></s><s class="x ico"></s>'
              '<s class="x ico"></s><s class="x ico"></s></div></div>',
    'gallery': '<div class="wf-grid g3 tight"><s class="x img"></s>'
               '<s class="x img"></s><s class="x img"></s>'
               '<s class="x img"></s><s class="x img"></s><s class="x img"></s></div>',
    'image': '<s class="t w40"></s><s class="x img tall"></s>',
    'callout': '<div class="wf-callout"><s class="ico"></s>'
               '<span><s class="b w50"></s><s class="b w100"></s></span></div>',
    'contacts': '<s class="t w40"></s><div class="wf-grid g3">'
                '<s class="x person"></s><s class="x person"></s><s class="x person"></s></div>',
    # People cards sit on their side: avatar left, name and role right, two
    # buttons under. Drawn that way so the preview matches what lands.
    'people': '<s class="t w40"></s><div class="wf-grid g3">'
              '<div class="wf-card row"><s class="av"></s>'
              '<span><s class="b w70"></s><s class="b w50 mut"></s></span></div>'
              '<div class="wf-card row"><s class="av"></s>'
              '<span><s class="b w70"></s><s class="b w50 mut"></s></span></div>'
              '<div class="wf-card row"><s class="av"></s>'
              '<span><s class="b w70"></s><s class="b w50 mut"></s></span></div></div>',
    'stats': '<div class="wf-stats"><span><s class="n"></s><s class="b w80 mut"></s></span>'
             '<span><s class="n"></s><s class="b w80 mut"></s></span>'
             '<span><s class="n"></s><s class="b w80 mut"></s></span>'
             '<span><s class="n"></s><s class="b w80 mut"></s></span></div>',
    'text-stats': '<div class="wf-card"><s class="b w50"></s><s class="b w100"></s>'
                  '<s class="b w80"></s><div class="wf-stats tight">'
                  '<span><s class="n"></s></span><span><s class="n"></s></span>'
                  '<span><s class="n"></s></span></div></div>',

    # ── lists ───────────────────────────────────────────────────────────
    'list': '<s class="t w40"></s><div class="wf-card">'
            '<div class="wf-line"><s class="pill"></s><s class="b w70"></s></div>'
            '<div class="wf-line"><s class="pill"></s><s class="b w60"></s></div>'
            '<div class="wf-line"><s class="pill"></s><s class="b w80"></s></div></div>',
    'events': '<s class="t w40"></s><div class="wf-card">'
              '<div class="wf-line"><s class="date"></s><s class="b w70"></s></div>'
              '<div class="wf-line"><s class="date"></s><s class="b w60"></s></div></div>',
    'agenda': '<div class="wf-card"><div class="wf-line"><s class="time"></s>'
              '<s class="b w70"></s></div><div class="wf-line"><s class="time"></s>'
              '<s class="b w60"></s></div></div>',
    'downloads': '<s class="t w40"></s><div class="wf-card">'
                 '<div class="wf-line"><s class="i"></s><s class="b w60"></s>'
                 '<s class="btn"></s></div><div class="wf-line"><s class="i"></s>'
                 '<s class="b w70"></s><s class="btn"></s></div></div>',
    'timeline': '<div class="wf-time"><div><s class="i"></s><s class="b w70"></s></div>'
                '<div><s class="i"></s><s class="b w60"></s></div>'
                '<div><s class="i"></s><s class="b w80"></s></div></div>',
    'accordion': '<div class="wf-card"><div class="wf-line"><s class="b w60"></s>'
                 '<s class="chev"></s></div><div class="wf-line open">'
                 '<s class="b w80"></s></div><div class="wf-line">'
                 '<s class="b w50"></s><s class="chev"></s></div></div>',
    'tabs': '<div class="wf-tabs"><div class="wf-side"><s class="b on"></s>'
            '<s class="b"></s><s class="b"></s></div>'
            '<div class="wf-pane"><s class="b w80"></s><s class="b w100"></s>'
            '<s class="b w60"></s></div></div>',
    'table': '<div class="wf-table"><div class="hd"><s></s><s></s><s></s></div>'
             '<div><s></s><s></s><s></s></div><div><s></s><s></s><s></s></div></div>',

    # ── media and actions ───────────────────────────────────────────────
    'video': '<div class="wf-video"><s class="play"></s></div>',
    'buttons': '<div class="wf-btns"><s class="btn on"></s><s class="btn"></s></div>',
    'reel': '<div class="wf-reel"><s class="t w50 on"></s><s class="b w30 on"></s>'
            '<s class="dots"></s></div>',
    'divider': '<div class="wf-div"><s></s></div>',
    'code': '<div class="wf-code"><s class="b w70"></s><s class="b w40"></s>'
            '<s class="b w80"></s></div>',
    'people': '<div class="wf-grid g4 tight"><s class="x face"></s>'
              '<s class="x face"></s><s class="x face"></s><s class="x face"></s></div>',
    'feature': '<div class="wf-feature"><s class="lock"></s><s class="b w60"></s>'
               '<s class="b w80"></s></div>',
}

#: block type -> shape. A type with no entry falls back to a plain text block,
#: which is closer to right than showing nothing.
SHAPE_FOR = {
    'row': 'columns',
    'page_head': 'page-head',
    'greeting': 'greeting',
    'rich_text': 'text',
    'announcement': 'announce',
    'hero': 'hero',
    'info_cards': 'cards3',
    'link_tiles': 'tiles6',
    'quick_links': 'tiles6',
    'icon_tiles': 'icon-tiles',
    'value_cards': 'values',
    'gallery': 'gallery',
    'contacts': 'contacts',
    'image': 'image',
    'callout': 'callout',
    'people': 'people',
    'stats': 'stats',
    'text_stats': 'text-stats',
    'news_list': 'list',
    'news_feed': 'list',
    'policy_list': 'downloads',
    'downloads': 'downloads',
    'event_list': 'events',
    'agenda': 'agenda',
    'timeline': 'timeline',
    'faq': 'accordion',
    'tabs': 'tabs',
    'table': 'table',
    'video': 'video',
    'buttons': 'buttons',
    'project_reel': 'reel',
    'birthdays': 'people',
    'community': 'list',
    'custom_html': 'code',
    'feature': 'feature',
}


#: Features are all one section type, so keying the preview off the type alone
#: gave nineteen identical boxes in the palette — the exact problem the previews
#: are here to solve. Each is keyed off what it actually shows instead.
WIDGET_SHAPE = {
    'news_index': 'list',
    'calendar': 'events',
    'policies': 'downloads',
    'brand_templates': 'downloads',
    'brand_colours': 'tiles6',
    'brand_assets': 'cards3',
    'booth': 'gallery',
    'portfolio': 'reel',
    'exchange': 'list',
    'my_aji': 'cards3',
    'directory': 'people',
    'cafeteria': 'list',
    'training': 'list',
    'ob_ceo': 'greeting',
    'ob_handbook': 'accordion',
    'pc_links': 'tiles6',
    'pc_hr': 'cards3',
    'pc_work': 'hero',
    'pc_explore': 'tabs',
}


def shape_markup(block_type, widget=''):
    """The wireframe for a palette entry.

    A feature's preview comes from its widget, because every feature shares one
    section type and would otherwise look identical to all the others.
    """
    if widget and widget in WIDGET_SHAPE:
        return SHAPES.get(WIDGET_SHAPE[widget], SHAPES['feature'])
    name = SHAPE_FOR.get(block_type, 'text')
    return SHAPES.get(name, SHAPES['text'])


#: A preset's wireframe shows the split *and* what lands in it. A bare two-column
#: outline looks identical for every preset, which is exactly the confusion the
#: presets exist to remove.
PRESET_SHAPES = {
    # Column splits. The column boxes are drawn with a background in the palette
    # so two-up and three-up do not look like the same stack of bars.
    'two-col': '<div class="wf-row"><div class="wf-col"><s class="b w100"></s>'
               '<s class="b w80"></s></div><div class="wf-col"><s class="b w100"></s>'
               '<s class="b w80"></s></div></div>',
    'text-image': '<div class="wf-row"><div class="wf-col"><s class="b w100"></s>'
                  '<s class="b w80"></s><s class="b w60"></s></div>'
                  '<div class="wf-col"><s class="x img"></s></div></div>',
    'image-text': '<div class="wf-row"><div class="wf-col"><s class="x img"></s></div>'
                  '<div class="wf-col"><s class="b w100"></s>'
                  '<s class="b w80"></s><s class="b w60"></s></div></div>',
    'main-side': '<div class="wf-row wide-l"><div class="wf-col"><s class="b w100"></s>'
                 '<s class="b w90"></s><s class="b w70"></s></div>'
                 '<div class="wf-col"><s class="x"></s><s class="x"></s></div></div>',
    'three-col': '<div class="wf-row g3"><div class="wf-col"><s class="b w100"></s>'
                 '<s class="b w70"></s></div>'
                 '<div class="wf-col"><s class="b w100"></s><s class="b w70"></s></div>'
                 '<div class="wf-col"><s class="b w100"></s><s class="b w70"></s></div></div>',
    'four-col': '<div class="wf-row g3"><div class="wf-col"><s class="x ico"></s></div>'
                '<div class="wf-col"><s class="x ico"></s></div>'
                '<div class="wf-col"><s class="x ico"></s></div>'
                '<div class="wf-col"><s class="x ico"></s></div></div>',

    # Whole openings and sections. Each shows what lands, in order.
    'page-start': '<s class="b w30 mut"></s><s class="t w70"></s>'
                  '<s class="b w90"></s><s class="b w100"></s><s class="b w60"></s>',
    'hero-start': '<div class="wf-hero"><s class="t w60 on"></s>'
                  '<s class="b w40 on"></s></div>'
                  '<div class="wf-grid g4 tight"><s class="x sm"></s><s class="x sm"></s>'
                  '<s class="x sm"></s><s class="x sm"></s></div>',
    'three-cards': '<s class="t w40"></s><div class="wf-grid g3">'
                   '<s class="x ico"></s><s class="x ico"></s><s class="x ico"></s></div>',
    'faq-block': '<s class="t w50"></s><div class="wf-faq"><s class="b w90"></s>'
                 '<s class="b w70"></s><s class="b w80"></s></div>',
    'team': '<s class="t w40"></s><div class="wf-grid g3">'
            '<div class="wf-card row"><s class="av"></s>'
            '<span><s class="b w70"></s><s class="b w50 mut"></s></span></div>'
            '<div class="wf-card row"><s class="av"></s>'
            '<span><s class="b w70"></s><s class="b w50 mut"></s></span></div>'
            '<div class="wf-card row"><s class="av"></s>'
            '<span><s class="b w70"></s><s class="b w50 mut"></s></span></div></div>',
    'docs': '<s class="t w50"></s><s class="b w80"></s>'
            '<div class="wf-rows"><s class="b w100"></s><s class="b w100"></s>'
            '<s class="b w100"></s></div>',
    'news-events': '<div class="wf-row"><div class="wf-col">'
                   '<s class="b w100"></s><s class="b w100"></s><s class="b w80"></s>'
                   '</div><div class="wf-col"><s class="x sm"></s><s class="x sm"></s>'
                   '</div></div>',
    'notice': '<div class="wf-callout"><s class="ico"></s>'
              '<span><s class="b w50"></s><s class="b w100"></s></span></div>'
              '<s class="b w100"></s><s class="b w70"></s>',
    'gallery-story': '<s class="t w40"></s><div class="wf-grid g3 tight">'
                     '<s class="x img"></s><s class="x img"></s><s class="x img"></s>'
                     '<s class="x img"></s><s class="x img"></s><s class="x img"></s></div>',
    'stats-row': '<div class="wf-card"><s class="b w90"></s>'
                 '<div class="wf-grid g3"><s class="t w60"></s><s class="t w60"></s>'
                 '<s class="t w60"></s></div></div>',
}


def preset_shape(key):
    """The wireframe for a ready-made layout."""
    return PRESET_SHAPES.get(key, SHAPES.get('row', SHAPES['text']))
