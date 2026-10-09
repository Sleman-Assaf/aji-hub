# cms/widgets.py
"""Page features: the working parts of the Hub, placed as blocks.

A feature renders the partial its page always rendered, using the host view's own
context. That means no view was refactored to make this work and no query moved,
so a converted page runs exactly the code it ran before; only its arrangement
became data.

The key is the only thing stored on a section, and it is looked up here, so a
content editor can never point a feature block at an arbitrary template.

Two kinds of feature, and conflating them was the problem
---------------------------------------------------------
Every feature used to be described as "locked" — the palette said so, the tree
said so, the settings drawer said so. But only four of them are actually
applications: the calendar grid, the cafeteria order form, the directory search
and the training approval workflow. Those really cannot become loose content
without a content edit being able to break them.

The other fifteen are *content panels*. They render records — brand assets,
albums, communities, HR updates — and around those records sat headings and
descriptions typed straight into the template, which no editor could reach.
Calling those "locked" was accurate but only because nobody had unlocked them.

So: SYSTEM is the short list that stays closed. Everything else declares its
editable text in LABELS, which is what makes those strings editable on the page
and stored on the block rather than compiled into a template.
"""

#: key -> (label, template, hint shown in the palette and the settings drawer)
WIDGETS = {
    'news_index': ('News index', 'content/_feature_news.html',
                   'The full article list with its filters, search and pagination.'),
    'calendar': ('Calendar', 'content/_feature_calendar.html',
                 'The month and week views with event management.'),
    # People & Culture, split so each section is its own block.
    'pc_links': ('P&C: quick buttons', 'content/_feature_people_culture_links.html',
                 'The row of shortcut buttons under the hero.'),
    'pc_hr': ('P&C: HR information', 'content/_feature_people_culture_hr.html',
              'The HR updates feed, with its management link for HR.'),
    'pc_work': ('P&C: work at AJi', 'content/_feature_people_culture_work.html',
                'The cards describing what it is like to work here.'),
    'pc_explore': ('P&C: explore', 'content/_feature_people_culture_explore.html',
                   'The onward links out of People & Culture.'),
    'portfolio': ('Project portfolio', 'content/_feature_portfolio.html',
                  'The project grid with its filters.'),
    'policies': ('SOPs & Manuals', 'portal/_feature_policies.html',
                 'Policy categories, documents and the management controls.'),
    # Brand Files, split into its three sections.
    'brand_templates': ('Brand: templates', 'portal/_feature_brand_templates.html',
                        'Document and presentation templates.'),
    'brand_colours': ('Brand: colour palette', 'portal/_feature_brand_colours.html',
                      'The brand colours with their values.'),
    'brand_assets': ('Brand: brand assets', 'portal/_feature_brand_assets.html',
                     'Guidelines, naming and the rebranding letters.'),

    # Onboarding. About AJi and Our Expertise are no longer here: their content
    # became editable blocks in Phase 2, so there is nothing left to lock.
    'ob_ceo': ('Onboarding: CEO letter', 'portal/_feature_onboarding_ceo.html',
               'The letter from the CEO.'),
    'ob_handbook': ('Onboarding: handbook banner',
                    'portal/_feature_onboarding_handbook.html',
                    'The download banner for the handbook PDF.'),
    'ob_handbook_tabs': ('Onboarding: handbook chapters',
                         'portal/_feature_onboarding_handbook_tabs.html',
                         'The chapters behind their row of tabs.'),
    'booth': ('AJi Booth', 'portal/_feature_booth.html',
              'Photo and video albums.'),
    'exchange': ('AJi Exchange', 'exchange/_feature_exchange.html',
                 'Communities and discussion threads.'),
    'my_aji': ('My AJi', 'services/_feature_my_aji.html',
               'Personal requests, approvals and service cards.'),
    'qr_codes': ('QR codes', 'people/_feature_qr.html',
                 'The WhatsApp channel QR codes, from the records HR manages.'),
    'directory': ('Employee directory', 'people/_feature_directory.html',
                  'Searchable staff list with entity and department filters.'),
    'cafeteria': ('Cafeteria ordering', 'cafeteria/_feature_cafeteria.html',
                  'The menu and the order form.'),
    'training': ('Training requests', 'training/_feature_training.html',
                 'The expression-of-interest list and its approval workflow.'),
}

#: The features that are applications rather than content, and stay closed.
#: A content edit inside any of these could break the thing it is for: a grid
#: that has to line dates up, an order form, a search, an approval chain.
SYSTEM = {'calendar', 'cafeteria', 'directory', 'training'}

#: The words inside a content panel that an editor may change, as
#: key -> (default, description). Anything listed here is rendered by
#: {% feature_text %}, which stores an override on the block itself, so the panel
#: keeps working exactly as before and the words stop being compiled in.
#:
#: Only keys listed here can be written, so an edit can never invent a setting.
LABELS = {
    'pc_explore': {
        'heading':        ('Explore People &amp; Culture', 'Heading above the tabbed panels'),
        'channels_head':  ('Internal Communication Channels', 'Heading above the channel tiles'),
        'growth_train':   ('Training &amp; Development', 'First card in My Growth'),
        'growth_certs':   ('Recommended Certifications', 'Second card in My Growth'),
        'growth_certs_intro': (
            'Certifications AJi encourages and reimburses across disciplines.',
            'Line under the certifications heading'),
        'voice_hotline':  ('CEO Hotline', 'First card in Employee Voice'),
        'voice_ideas':    ('Suggestion Box / Ideas', 'Second card in Employee Voice'),
        'voice_surveys':  ('Employee Surveys', 'Third card in Employee Voice'),
        'voice_surveys_intro': ('Open surveys appear here.',
                                'Line under the surveys heading'),
        # Rendered by feature_rich: a list whose length is the editor's business,
        # not something frozen by however many <li> the template happened to have.
        'growth_train_list': (
            '<ul><li>Training &amp; Development policy</li>'
            '<li>Upskilling Program</li>'
            '<li>Certification &amp; Reimbursement</li>'
            '<li>Expression of Interest for trainings</li>'
            '<li>Training procedures</li></ul>',
            'What Training &amp; Development covers'),
        'wellness_intro': (
            'AJi Group is committed to fostering a healthy and productive '
            'workplace. Our wellness program is structured quarterly, focusing on '
            'specific initiatives each quarter to maximise engagement and impact.',
            'Intro above the wellness programmes'),
        'wellness_empty': ('Wellness program details coming soon.',
                           'Shown when no programmes are published'),
        'childcare_title': ('"Our Childcare Support is Here to Help"',
                            'Childcare panel heading'),
        'childcare_intro': (
            'At AJi, we support working parents by offering discounted childcare '
            'rates through partnerships with local providers. This eases the '
            'financial and logistical challenges of childcare, letting employees '
            'focus on their careers while ensuring quality care for their '
            'children.',
            'Childcare panel intro'),
        'childcare_elig_head':  ('Eligibility', 'Childcare: first column heading'),
        'childcare_elig': (
            '<ul><li>All full-time AJi employees are eligible.</li>'
            '<li>Discounts apply to employees with children enrolled in '
            'participating centres.</li>'
            '<li>Contact the provider directly with proof of employment.</li></ul>',
            'Childcare: who is eligible'),
        'childcare_how_head':   ('How it works', 'Childcare: second column heading'),
        'childcare_how': (
            '<ul><li>Review the list of participating providers.</li>'
            '<li>Contact your preferred provider about enrollment and discounts.</li>'
            '<li>Present proof of employment to qualify.</li>'
            '<li>Payment is arranged directly with the provider.</li></ul>',
            'Childcare: the steps'),
        'childcare_terms_head': ('Terms &amp; conditions', 'Childcare: third column heading'),
        'childcare_terms': (
            '<ul><li>AJi facilitates partnerships but does not cover fees.</li>'
            "<li>Discounts depend on each provider's agreement and may change.</li>"
            '<li>AJi may update the initiative after future evaluations.</li></ul>',
            'Childcare: the small print'),
        'childcare_partners_head': ('Partner Nurseries',
                                    'Heading above the nursery chips'),
        'voice_hotline_text': (
            'A confidential channel linked directly to the CEO, Eng. Hamzeh Awwad. '
            'Share any concerns or feedback to help create a positive, inclusive '
            'workplace.',
            'What the CEO Hotline is for'),
        'voice_ideas_text': (
            'Share your ideas, suggestions or feedback — submissions are '
            'completely anonymous. HR reviews ideas without seeing who '
            'submitted them.',
            'What the Suggestion Box is for'),
        'ch_wa':        ('WhatsApp Channels', 'First channel tile'),
        'ch_wa_sub':    ("Scan to join AJi's official channels",
                         'First channel tile, second line'),
        'ch_ext':       ('Phone Extension List', 'Second channel tile'),
        'ch_ext_sub':   ('Internal directory of extensions',
                         'Second channel tile, second line'),
        'ch_chat':      ('Intranet Chat', 'Third channel tile'),
        'ch_chat_sub':  ('Message any colleague on AJi Hub',
                         'Third channel tile, second line'),
    },
    'policies': {
        'other_head': ('Other Documents', 'Heading above the uncategorised list'),
        'other_sub':  ('Additional policies and procedures',
                       'Line under that heading'),
        'cat_empty':  ('Nothing here yet — no folders and no documents.',
                       'Shown for a library or folder with nothing in it'),
        'empty':      ('No policies published yet. Add categories and documents '
                       'via the admin panel.',
                       'Shown when the library is empty'),
    },
    'ob_handbook': {
        'heading': ('Employee Handbook', 'Heading above the handbook banner'),
    },
    'ob_handbook_tabs': {
        'explore_head': ('Explore the Handbook', 'Heading above the chapter tabs'),
        'hours':        ('Normal office hours · 08:00 – 17:00',
                         'Attendance banner, first line'),
        'hours_note':   ('Attendance recorded via the reception punch clock',
                         'Attendance banner, second line'),
    },
    'ob_ceo': {
        'heading': ('Words from our CEO', 'Heading on the letter card'),
    },
    'exchange': {
        'mine_head':     ('My communities', 'Heading above the reader’s own communities'),
        'discover_head': ('Discover more',
                          'Heading above the rest, when the reader has joined some'),
        'discover_head_alone': ('Communities',
                                'Heading when the reader has joined none'),
        'empty':         ('No more communities to discover.',
                          'Shown when there is nothing left to join'),
    },
    'booth': {
        'empty': ('No albums yet.', 'Shown when there are no albums'),
    },
    'my_aji': {
        'empty': ('No services are available for your role yet.',
                  'Shown when the reader’s role has no services'),
    },
    'portfolio': {
        'empty': ('No projects match these filters.',
                  'Shown when the filters match nothing'),
    },
    'qr_codes': {
        'join':        ('Join', 'Label on the invite-link button'),
        'empty':       ('No channels have been added yet.',
                        'Shown when there are no channels'),
        'manage_note': ('Codes expire when a group is recreated.',
                        'Note shown to HR above the manage link'),
    },
}

#: What each panel lists, for the button that offers its records. The *URL* is
#: not here: content_admin.ANCHORS already holds where every feature's content is
#: managed, and a second table of the same thing is how the toolbar and the
#: drawer end up disagreeing about where People & Culture content lives.
RECORDS = {
    'news_index':      'Articles',
    'pc_hr':           'HR updates',
    'pc_explore':      'Tabs, certifications and channels',
    'pc_work':         'The handbook entry',
    'pc_links':        'The quick buttons',
    'portfolio':       'Projects',
    'policies':        'Policy documents',
    'brand_templates': 'Brand templates',
    'brand_colours':   'Brand colours',
    'brand_assets':    'Brand assets',
    'ob_ceo':          'The CEO letter',
    'ob_handbook':      'The handbook PDF',
    'ob_handbook_tabs': 'Handbook chapters',
    'booth':           'Albums',
    'exchange':        'Communities',
    'my_aji':          'Service categories',
    'qr_codes':        'Channels and their codes',
}


def widget_choices():
    return [(key, meta[0]) for key, meta in sorted(WIDGETS.items(),
                                                   key=lambda kv: kv[1][0])]


def widget_key(section):
    return (section.config or {}).get('widget', '')


def widget_meta(section):
    """(label, template, hint) for a feature block, or None if unset/unknown."""
    return WIDGETS.get(widget_key(section))


def widget_template(section):
    meta = widget_meta(section)
    return meta[1] if meta else ''


def is_system(key):
    """True for the four features that are applications, not content."""
    return key in SYSTEM


def is_locked(section):
    """True when this block's insides genuinely cannot be opened.

    A feature block whose widget is unset or unrecognised counts as locked too:
    there is nothing to edit and no way to know what it was meant to be.
    """
    key = widget_key(section)
    return is_system(key) or key not in WIDGETS


def labels_for(key):
    """{key: (default, description)} — the editable words in one panel."""
    return LABELS.get(key, {})


def records_for(key):
    """(url, what it manages) for a panel's records, or None.

    The URL comes from content_admin, which is where every "manage this" link on
    the Hub is already resolved.
    """
    from .content_admin import anchor_for
    label = RECORDS.get(key)
    url = anchor_for(key)
    return (url, label) if (url and label) else None
