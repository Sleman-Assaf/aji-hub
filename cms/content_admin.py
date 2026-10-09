# cms/content_admin.py
"""Which page's content is managed where.

Editing was scattered: some things were edited on the page, some in a CMS form,
and some on a management screen belonging to a different section entirely —
People & Culture content was reachable only from a URL under /onboarding/. Nobody
should have to remember any of that, so the mapping lives here once and the edit
toolbar reads it to offer the right screen for whatever page you are on.

Keyed on the page's bound path. A page with no entry simply gets no button,
rather than a link to somewhere unrelated.
"""

#: bound path → (management URL, what the button says)
SCREENS = {
    '/people-culture/': ('/people-culture/manage/', "Manage People & Culture content"),
    '/onboarding/':     ('/onboarding/manage/',     'Manage onboarding content'),
    '/policies/':       ('/policies/libraries/',    'Manage document libraries'),
    '/brand/':          ('/brand/manage/',          'Manage brand files'),
    '/booth/':          ('/booth/manage/',          'Manage albums'),
    '/portfolio/':      ('/portfolio/manage/',      'Manage projects'),
    '/news/':           ('/news/',                  'Manage news'),
    '/calendar/':       ('/calendar/',              'Manage events'),
}

#: Anchors on a management screen, so a link from a section on the page can land
#: on that section rather than at the top of a long form.
#:
#: This is the one place a feature's records are located. The settings drawer
#: reads it too, so a panel opened in the editor offers the same destination the
#: toolbar does — rather than a second table of URLs that can drift out of step
#: with this one.
ANCHORS = {
    'pc_work':     '/people-culture/manage/#handbook',
    'pc_explore':  '/people-culture/manage/#wellness',
    'pc_hr':       '/people-culture/hr/',
    'ob_ceo':      '/onboarding/manage/#ceo',
    'ob_handbook':      '/onboarding/manage/#handbook',
    'ob_handbook_tabs': '/onboarding/manage/#ob-items',
    # The rest of the content panels. Each lands on the screen that owns its
    # records, which for several of these is not the screen their page is on.
    'news_index':      '/news/',
    'portfolio':       '/portfolio/manage/#projects',
    'policies':        '/policies/manage/',
    'brand_templates': '/brand/manage/',
    'brand_colours':   '/brand/manage/#colours',
    'brand_assets':    '/brand/manage/',
    'booth':           '/booth/manage/#albums',
    'exchange':        '/exchange/',
    'my_aji':          '/my-aji/',
    'pc_links':        '/people-culture/manage/',
    'qr_codes':        '/people-culture/manage/#whatsapp',
}


def screen_for(path):
    """The management screen for a bound path, or None when there isn't one."""
    if not path:
        return None
    return SCREENS.get(path if path.endswith('/') else path + '/')


def anchor_for(widget):
    """Where a feature's own content is managed, or None."""
    return ANCHORS.get(widget or '')


#: Which screen owns each section, and the anchor to land on. Saving used to send
#: everyone to the onboarding screen regardless of what they had edited, because
#: every handler redirected to the one view that existed before the split.
SECTION_HOME = {
    'handbook':       ('/people-culture/manage/', 'handbook'),
    'wellness':       ('/people-culture/manage/', 'wellness'),
    'childcare':      ('/people-culture/manage/', 'childcare'),
    'certifications': ('/people-culture/manage/', 'certifications'),
    'whatsapp':       ('/people-culture/manage/', 'whatsapp'),
    'tabs':           ('/people-culture/manage/', 'tabs'),
    'ceo':            ('/onboarding/manage/',     'ceo'),
    'ob-items':       ('/onboarding/manage/',     'ob-items'),
    # The other pages' screens. Booth's handlers used to redirect to the Brand
    # screen: booth management was built alongside brand management and inherited
    # its redirect, so saving an album sent you to Brand Files.
    'policies':       ('/policies/manage/',       ''),
    'brand-files':    ('/brand/manage/',          ''),
    'brand-colours':  ('/brand/manage/',          'colours'),
    'albums':         ('/booth/manage/',          'albums'),
    'projects':       ('/portfolio/manage/',      'projects'),
}


def back_to(section):
    """Where to land after saving that section, anchored to it."""
    url, anchor = SECTION_HOME.get(section, ('/onboarding/manage/', ''))
    return f'{url}#{anchor}' if anchor else url
