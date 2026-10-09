# cms/models.py
"""Database-driven pages for AJi Hub.

A Page is an ordered list of Sections; each Section renders through a template
chosen from a fixed library and is filled from its own fields plus its child
SectionItems. Nothing here introduces new visual language — every section
template reuses the classes already in aji_hub.css, so pages built with the CMS
look like the rest of the Hub.

Scheduling is evaluated at read time (publish_at / archive_at) so no background
worker is required.
"""
from django.contrib.auth.models import Group, User
from django.db import models, transaction
from django.utils import timezone

CONTENT_EDITORS_GROUP = 'Content Editors'
EXECUTIVE_GROUP = 'Executive'


class Audience(models.TextChoices):
    """Who may see a page or section. Concrete roles are assigned later by
    dropping users into groups; the mechanism is in place now."""
    EVERYONE = 'everyone', 'Everyone'
    HR       = 'hr',       'HR only'
    MANAGERS = 'managers', 'Managers only'
    EXEC     = 'exec',     'Executive only'
    GROUPS   = 'groups',   'Specific groups'


def _user_is_manager(user):
    """Someone with at least one direct report in the employee tree."""
    try:
        employee = user.employee
    except Exception:
        return False
    return employee is not None and employee.direct_reports.exists()


def audience_allows(audience, groups_qs, user):
    """Shared visibility test for Page and Section."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    from core.models import user_is_hr

    if audience == Audience.EVERYONE:
        return True
    if audience == Audience.HR:
        return user_is_hr(user)
    if audience == Audience.MANAGERS:
        return _user_is_manager(user) or user_is_hr(user)
    if audience == Audience.EXEC:
        return user.groups.filter(name=EXECUTIVE_GROUP).exists()
    if audience == Audience.GROUPS:
        return groups_qs.filter(pk__in=user.groups.values('pk')).exists()
    return False


class ScheduledQuerySet(models.QuerySet):
    def live(self, now=None):
        """Rows whose scheduling window is currently open."""
        now = now or timezone.now()
        return self.filter(
            models.Q(publish_at__isnull=True) | models.Q(publish_at__lte=now)
        ).filter(
            models.Q(archive_at__isnull=True) | models.Q(archive_at__gt=now)
        )


class ScheduledModel(models.Model):
    """publish_at / archive_at, honoured when reading rather than by a job."""
    publish_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Leave empty to show immediately. Otherwise it appears at this time.')
    archive_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Leave empty to keep indefinitely. Otherwise it hides at this time.')

    objects = ScheduledQuerySet.as_manager()

    class Meta:
        abstract = True

    @property
    def is_scheduled(self):
        return bool(self.publish_at and self.publish_at > timezone.now())

    @property
    def is_archived(self):
        return bool(self.archive_at and self.archive_at <= timezone.now())

    @property
    def schedule_state(self):
        if self.is_archived:
            return 'archived'
        if self.is_scheduled:
            return 'scheduled'
        return 'live'


class Page(ScheduledModel):
    """A CMS-managed page, served at /p/<slug>/."""
    title    = models.CharField(max_length=200)
    slug     = models.SlugField(unique=True, help_text='URL: /p/<slug>/')
    eyebrow  = models.CharField(max_length=80, blank=True,
                                help_text='Small label above the heading.')
    subtitle = models.CharField(max_length=400, blank=True)

    is_published = models.BooleanField(default=False)
    audience = models.CharField(max_length=20, choices=Audience.choices,
                                default=Audience.EVERYONE)
    audience_groups = models.ManyToManyField(
        Group, blank=True, related_name='cms_pages',
        help_text='Used when the audience is "Specific groups".')

    show_in_nav = models.BooleanField(
        default=False, help_text='List this page on the Hub pages index.')
    nav_order = models.IntegerField(default=0)

    # Binding a page to an existing route (e.g. /brand/) lets CMS sections be
    # attached to a hardcoded template without rewriting it. That is what makes
    # the edit toolbar meaningful on every page rather than only on /p/<slug>/.
    bound_path = models.CharField(
        max_length=200, blank=True, null=True, unique=True, db_index=True,
        help_text='Existing Hub route this page manages, e.g. "/brand/". '
                  'Leave empty for a standalone page served at /p/<slug>/.')

    # ── Navigation context ───────────────────────────────────────────────
    # Set explicitly rather than derived from the URL, because a route's shape
    # and its place in the information architecture are not the same thing:
    # /onboarding/ belongs under People & Culture, which no URL says.
    parent = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='child_pages',
                               help_text='The page this one sits under, for breadcrumbs.')
    crumb_label = models.CharField(
        max_length=80, blank=True,
        help_text='Shorter name for the breadcrumb trail. Defaults to the title.')
    show_crumbs = models.BooleanField(
        default=True, help_text='Show the breadcrumb trail on this page.')

    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL,
                                   null=True, blank=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # ── Drafts ───────────────────────────────────────────────────────────
    # Editing used to write straight to the page every reader was looking at, so
    # a half-finished heading was live the moment it was typed. A draft is a
    # copy of the page and all its blocks; editing touches only the copy, and
    # readers keep seeing the published one until somebody presses Save.
    #
    # A draft is a Page with draft_of set. It is never bound to a route and never
    # published, so nothing that resolves a route or lists pages can reach one.
    draft_of = models.ForeignKey('self', on_delete=models.CASCADE, null=True,
                                 blank=True, related_name='drafts',
                                 editable=False)
    draft_started_by = models.ForeignKey(User, on_delete=models.SET_NULL,
                                         null=True, blank=True, editable=False,
                                         related_name='started_drafts')
    draft_started_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['nav_order', 'title']
        permissions = [('publish_page', 'Can publish and schedule pages')]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        from django.urls import reverse
        # A bound page lives at the route it manages, not under /p/.
        if self.bound_path:
            return self.bound_path
        return reverse('cms_page', args=[self.slug])

    @property
    def is_bound(self):
        return bool(self.bound_path)

    @classmethod
    def for_path(cls, path):
        """The page managing `path`, if one exists. Used by the edit toolbar."""
        if not path:
            return None
        candidates = {path}
        # Tolerate a missing or extra trailing slash.
        candidates.add(path.rstrip('/') + '/')
        candidates.add(path.rstrip('/'))
        # draft_of__isnull: a draft is never the page a route resolves to, even
        # if a bound_path somehow ended up copied onto one.
        return (cls.objects.filter(bound_path__in=[c for c in candidates if c],
                                   draft_of__isnull=True)
                .prefetch_related('audience_groups').first())

    def is_visible_to(self, user):
        """Whether this page's content should be shown to `user`.

        For a bound page this governs only its own sections — the host route's
        view still decides who may reach the page at all.
        """
        if not self.is_published or self.schedule_state != 'live':
            return user_can_edit_cms(user)
        return audience_allows(self.audience, self.audience_groups, user)

    @property
    def crumb(self):
        return self.crumb_label or self.title

    def trail(self):
        """Ancestors then self, root first, for the breadcrumb bar.

        Guarded against a page being made its own ancestor: a cycle here would
        hang every page that renders a trail, not just the misconfigured one.
        """
        chain, node, seen = [], self, set()
        while node is not None and node.pk not in seen:
            seen.add(node.pk)
            chain.append(node)
            node = node.parent
        return list(reversed(chain))

    # ── Draft lifecycle ──────────────────────────────────────────────────

    @property
    def is_draft(self):
        return self.draft_of_id is not None

    @property
    def live_page(self):
        """The published page this one belongs to — itself, if it is not a draft."""
        return self.draft_of if self.draft_of_id else self

    def open_draft(self):
        """The draft of this page, or None."""
        if self.is_draft:
            return self
        return self.drafts.first()

    def start_draft(self, user=None):
        """Fork a draft of this page, or return the one already open.

        One draft per page on purpose. If two people edit at once they work on
        the same copy and see each other's changes, which is confusing but
        recoverable; two drafts racing to publish would silently discard one
        person's afternoon.
        """
        from django.utils import timezone

        if self.is_draft:
            return self
        existing = self.drafts.first()
        if existing is not None:
            return existing

        draft = Page.objects.get(pk=self.pk)
        draft.pk = None
        draft.id = None
        # A draft must not be reachable: not on a route, not published, and its
        # slug must not collide with a real page's.
        draft.bound_path = None
        draft.is_published = False
        draft.show_in_nav = False
        draft.slug = f'draft-{self.pk}-{timezone.now().timestamp():.0f}'[:50]
        draft.draft_of = self
        draft.draft_started_by = user if (user and user.is_authenticated) else None
        draft.draft_started_at = timezone.now()
        draft.save()
        draft.audience_groups.set(self.audience_groups.all())

        copy_sections(self, draft)
        return draft

    @transaction.atomic
    def publish_draft(self, user=None):
        """Put the draft's content live and throw the draft away.

        The page's own settings come across too — a title changed in the Page tab
        while drafting should land with everything else rather than having gone
        live on its own.
        """
        draft = self.open_draft()
        if draft is None or draft.pk == self.pk:
            return False

        for field in ('title', 'eyebrow', 'subtitle', 'audience', 'crumb_label',
                      'show_crumbs', 'nav_order'):
            setattr(self, field, getattr(draft, field))
        self.updated_by = user if (user and user.is_authenticated) else None
        self.save()
        self.audience_groups.set(draft.audience_groups.all())

        merge_sections(draft, self)
        draft.delete()
        return True

    def discard_draft(self):
        """Throw the draft away. The published page was never touched."""
        draft = self.open_draft()
        if draft is None or draft.pk == self.pk:
            return False
        draft.delete()
        return True

    def visible_sections(self, user):
        """Live, permitted top-level sections in display order (pinned first).

        parent__isnull filters out blocks that live inside a columns row — those
        are rendered by their row, and without this they would appear twice.
        """
        qs = (self.sections.filter(is_visible=True, parent__isnull=True,
                                   deleted_at__isnull=True).live()
              .prefetch_related('items', 'audience_groups',
                                'children__items', 'children__audience_groups'))
        return [s for s in qs if s.is_visible_to(user)]


class SectionType(models.TextChoices):
    """The template library. Each value maps to cms/sections/<value>.html and
    is rendered with the Hub's existing components."""
    HERO         = 'hero',         'Hero banner'
    RICH_TEXT    = 'rich_text',    'Text block'
    ANNOUNCEMENT = 'announcement', 'Announcement'
    INFO_CARDS   = 'info_cards',   'Information cards'
    QUICK_LINKS  = 'quick_links',  'Quick links'
    STATS        = 'stats',        'Statistics'
    DOWNLOADS    = 'downloads',    'Downloads / document library'
    GALLERY      = 'gallery',      'Image gallery'
    #: A single picture. The library had a gallery and a hero but no way to place
    #: one image, so editors were using a one-item gallery to do it.
    IMAGE        = 'image',        'Image'
    #: A short piece of text that needs to stand out -- a note, a warning, a
    #: deadline. Announcement is a dated notice; this is a line in the flow.
    CALLOUT      = 'callout',      'Callout'
    CONTACTS     = 'contacts',     'Contacts'
    #: People cards — a name, a role, and a way to reach them. Each card can be
    #: linked to a colleague so the details follow the directory instead of being
    #: retyped here.
    PEOPLE       = 'people',       'People cards (meet the team)'
    FAQ          = 'faq',          'FAQ / accordion'
    TIMELINE     = 'timeline',     'Timeline'
    BUTTONS      = 'buttons',      'Button group'
    TABLE        = 'table',        'Table'
    VIDEO        = 'video',        'Video'
    NEWS_FEED    = 'news_feed',    'Latest news'
    POLICY_LIST  = 'policy_list',  'Policy list'
    CUSTOM_HTML  = 'custom_html',  'Custom HTML'

    # ── Layout ───────────────────────────────────────────────────────────
    #: A container that splits the page into columns. Its children carry a
    #: `column` index. Nothing else about a section changes when it sits in a
    #: column, so any block below can be placed inside one.
    ROW = 'row', 'Columns (layout)'

    #: A page's working part — the calendar grid, the order form, the directory
    #: search. Movable and retitlable, deliberately not openable, so arranging a
    #: page can never break the application on it.
    # Not "locked" any more: four of the nineteen are, and the rest are
    # content panels. The label showed on every one of them, in the palette,
    # the tree and the block toolbar.
    FEATURE = 'feature', 'Page feature'

    # ── The Hub's own sections, promoted to templates ─────────────────────
    # These render the exact markup the pages already use, so placing one
    # produces the component you can see on the site rather than a generic
    # approximation. Named for what they are, not for the page they came
    # from, because they are reusable anywhere.
    #: Every page had its eyebrow, title and intro hardcoded in its template.
    #: As a block they become editable, and the page can be rearranged around
    #: them like anything else.
    PAGE_HEAD    = 'page_head',     'Page heading (eyebrow, title, intro)'
    GREETING     = 'greeting',      'Greeting header (name, date, clock)'
    LINK_TILES   = 'link_tiles',    'Link tiles (bordered grid)'
    NEWS_LIST    = 'news_list',     'News list (compact rows)'
    TAQDEER      = 'taqdeer',       'AJi Taqdeer (recognition)'
    NEEDS_YOU    = 'needs_you',     'Waiting on you (personal)'
    EVENT_LIST   = 'event_list',    'Event list (date blocks)'
    AGENDA       = 'agenda',        'Agenda (times down the side)'
    PROJECT_REEL = 'project_reel',  'Project carousel'
    #: AJi Booth's albums, as a strip of covers. The photos were reachable only
    #: from /booth/, which is a page you have to think to visit — so 108 pictures
    #: of the company's own year sat where nobody passed them.
    BOOTH_REEL   = 'booth_reel',    'AJi Booth photo strip'

    #: Content that was a literal list in a view. Each row is a record now, so a
    #: statistic, value or discipline can be edited, reordered, given its own icon
    #: or hidden without a deploy.
    TEXT_STATS   = 'text_stats',    'Text with statistics'
    VALUE_CARDS  = 'value_cards',   'Value cards'
    ICON_TILES   = 'icon_tiles',    'Icon tiles'
    TABS         = 'tabs',          'Tabbed panels'
    BIRTHDAYS    = 'birthdays',     'Birthdays today'
    COMMUNITY    = 'community',     'Community chips and threads'


class Layout(models.TextChoices):
    """Column splits available to a ROW section."""
    FULL     = 'full',     'Full width'
    HALF     = '50-50',    'Two columns — 50 / 50'
    WIDE_L   = '55-45',    'Two columns — 55 / 45'
    WIDE_R   = '45-55',    'Two columns — 45 / 55'
    THIRD_R  = '33-66',    'Two columns — 33 / 66'
    THIRD_L  = '66-33',    'Two columns — 66 / 33'
    QUART_R  = '25-75',    'Two columns — 25 / 75'
    QUART_L  = '75-25',    'Two columns — 75 / 25'
    THIRDS   = 'thirds',   'Three columns — equal'
    # The homepage's content row: a picture beside two lists. Equal thirds gave
    # the photograph the same width as a column of one-line rows, which left it
    # too small to read and the lists wider than their content needed.
    THIRDS_L = 'thirds-l', 'Three columns — wide first'
    QUARTERS = 'quarters', 'Four columns — equal'


#: The CSS tracks for each split. One dict so the renderer, the layout picker
#: and its little preview icon can never drift apart.
LAYOUT_TRACKS = {
    Layout.FULL: '1fr',
    Layout.HALF: '1fr 1fr',
    Layout.WIDE_L: '1.1fr 1fr',
    Layout.WIDE_R: '1fr 1.1fr',
    Layout.THIRD_R: '1fr 2fr',
    Layout.THIRD_L: '2fr 1fr',
    Layout.QUART_R: '1fr 3fr',
    Layout.QUART_L: '3fr 1fr',
    Layout.THIRDS: 'repeat(3,1fr)',
    Layout.THIRDS_L: '1.45fr 1fr 1fr',
    Layout.QUARTERS: 'repeat(4,1fr)',
}

#: How many columns each layout exposes, so the editor and the renderer agree.
LAYOUT_COLUMNS = {
    Layout.FULL: 1, Layout.HALF: 2, Layout.WIDE_L: 2, Layout.WIDE_R: 2,
    Layout.THIRD_R: 2, Layout.THIRD_L: 2, Layout.QUART_R: 2, Layout.QUART_L: 2,
    Layout.THIRDS: 3, Layout.THIRDS_L: 3, Layout.QUARTERS: 4,
}


def layout_options():
    """Everything the layout picker needs, including its preview geometry."""
    return [{'value': value, 'label': label,
             'tracks': LAYOUT_TRACKS[value],
             'bars': range(LAYOUT_COLUMNS[value])}
            for value, label in Layout.choices]


#: Section types whose child rows are meaningful, shown as a hint in admin.
TYPES_WITH_ITEMS = {
    SectionType.INFO_CARDS, SectionType.QUICK_LINKS, SectionType.STATS,
    SectionType.DOWNLOADS, SectionType.GALLERY, SectionType.CONTACTS,
    SectionType.FAQ, SectionType.TIMELINE, SectionType.BUTTONS,
    SectionType.TABLE, SectionType.LINK_TILES,
    SectionType.TEXT_STATS, SectionType.VALUE_CARDS, SectionType.ICON_TILES,
    SectionType.TABS,
    SectionType.PAGE_HEAD,   # its rows are the header's primary actions
}

#: Types that read live Hub data instead of stored content. Their editable
#: settings are limits and links, not text, so the editor offers those instead
#: of an empty content form.
DATA_DRIVEN_TYPES = {
    SectionType.NEWS_LIST, SectionType.EVENT_LIST, SectionType.AGENDA,
    SectionType.PROJECT_REEL, SectionType.BIRTHDAYS, SectionType.COMMUNITY,
    SectionType.NEWS_FEED, SectionType.POLICY_LIST, SectionType.GREETING,
    SectionType.TAQDEER, SectionType.NEEDS_YOU,
}



#: A block's picture, whether it came from the media library or from an upload
#: made before the library existed.
class _PictureMixin(models.Model):
    """Resolves an image from the library first, then a legacy direct upload.

    Both are kept: every image uploaded before the library existed still works,
    and nothing had to be migrated for the library to be useful. A library image
    also carries its own alt text and focal point, so the same picture is described
    once no matter how many pages use it.
    """
    image_asset = models.ForeignKey(
        'dam.Asset', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
        help_text='Chosen from the media library. Takes precedence over an upload.')

    class Meta:
        abstract = True

    @property
    def picture(self):
        """{'url', 'alt', 'focal'} or None — what a template needs to draw it."""
        asset = self.image_asset
        if asset is not None:
            try:
                return {'url': asset.file.url, 'alt': asset.alt_text,
                        'focal': asset.focal_css}
            except Exception:
                pass
        legacy = getattr(self, 'image', None)
        if legacy:
            try:
                return {'url': legacy.url, 'alt': '', 'focal': '50% 50%'}
            except Exception:
                pass
        return None


class LiveSectionQuerySet(ScheduledQuerySet):
    """Sections that have not been removed."""
    def deleted(self):
        return self.filter(deleted_at__isnull=False)


class SectionManager(models.Manager.from_queryset(LiveSectionQuerySet)):
    """The default manager, which hides removed blocks.

    Filtering here rather than at each call site is deliberate: a soft delete that
    every query has to remember to exclude is a soft delete that leaks. Anything
    needing the removed rows — the page manager's restore list, restore itself —
    asks for `Section.all_objects`.
    """
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class Section(ScheduledModel, _PictureMixin):
    page = models.ForeignKey(Page, on_delete=models.CASCADE, related_name='sections')
    type = models.CharField(max_length=30, choices=SectionType.choices,
                            default=SectionType.RICH_TEXT)

    title    = models.CharField(max_length=200, blank=True)
    subtitle = models.CharField(max_length=400, blank=True)
    body     = models.TextField(blank=True, help_text='Used by text, announcement, custom HTML.')
    icon     = models.CharField(max_length=40, blank=True,
                                help_text='Icon name — pick from the icon list.')
    image    = models.ImageField(upload_to='cms/sections/', blank=True, null=True)

    # ── Nesting and layout ───────────────────────────────────────────────
    # A ROW section holds children; each child records which column it sits in.
    # One self-FK covers both "sections stacked on a page" and "blocks inside a
    # column", so the editor moves a block between the two without conversion.
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True,
                               related_name='children',
                               help_text='Set when this block sits inside a columns row.')
    column = models.PositiveSmallIntegerField(
        default=0, help_text='Which column of the parent row, left to right.')
    layout = models.CharField(max_length=10, choices=Layout.choices, default=Layout.FULL,
                              help_text='Column split — only meaningful on a columns row.')
    equal_height = models.BooleanField(
        default=False,
        help_text='Make every column in this row end at the same height.')

    order     = models.IntegerField(default=0)
    is_visible = models.BooleanField(default=True)
    pinned     = models.BooleanField(default=False, help_text='Keep at the top of the page.')

    # Deleting a block removes it from the page but keeps the row, so the action
    # can be undone. A block with children takes them with it and brings them
    # back together. Purged for real from the page manager, not from the editor,
    # because the editor is where the accident happens.
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)

    #: Which live block this one was copied from, on a draft. Publishing updates
    #: that block in place rather than deleting the page and rebuilding it, so a
    #: block keeps its primary key across a publish — anything holding one, from
    #: the removed-blocks list to a link somebody saved, still resolves.
    origin = models.ForeignKey('self', on_delete=models.SET_NULL, null=True,
                               blank=True, editable=False,
                               related_name='draft_copies')

    audience = models.CharField(max_length=20, choices=Audience.choices,
                                default=Audience.EVERYONE)
    audience_groups = models.ManyToManyField(
        Group, blank=True, related_name='cms_sections',
        help_text='Used when the audience is "Specific groups".')

    # Per-type knobs (columns, limits, embed URL…). Kept as JSON so a new
    # section template never needs a schema migration.
    config = models.JSONField(default=dict, blank=True)

    # Raw HTML runs for every employee who opens the page, so it is only
    # rendered unescaped when a superuser saved it. A content editor can draft
    # it; it stays escaped until someone with staff rights signs it off.
    html_approved = models.BooleanField(
        default=False, editable=False,
        help_text='Custom HTML is only rendered raw once a superuser saves it.')

    objects = SectionManager()
    #: Includes removed blocks. Used by the restore list and by restore itself.
    all_objects = models.Manager.from_queryset(LiveSectionQuerySet)()

    class Meta:
        ordering = ['-pinned', 'order', 'pk']
        # The base manager must see everything, or a related lookup from a
        # removed block's child would fail rather than resolve.
        base_manager_name = 'all_objects'

    def __str__(self):
        return f"{self.get_type_display()} — {self.title or self.page.title}"

    @property
    def template_path(self):
        return f'cms/sections/{self.type}.html'

    @property
    def takes_items(self):
        return self.type in TYPES_WITH_ITEMS

    @property
    def body_rendered(self):
        """The body as safe HTML.

        Bodies written before inline editing existed are plain text with
        newlines, so those are paragraphed on the way out. Bodies edited in place
        already carry markup and are sanitised on save — sanitised again here,
        because a row can also be written by a migration or the admin.
        """
        from django.utils.safestring import mark_safe
        from django.utils.html import linebreaks
        from .richtext import looks_like_html, sanitise_html

        if not self.body:
            return ''
        if looks_like_html(self.body):
            return mark_safe(sanitise_html(self.body))
        return mark_safe(linebreaks(self.body))

    @property
    def is_row(self):
        return self.type == SectionType.ROW

    @property
    def column_count(self):
        return LAYOUT_COLUMNS.get(self.layout, 1)

    @property
    def grid_template(self):
        """The CSS grid-template-columns value for this row's split."""
        return LAYOUT_TRACKS.get(self.layout, '1fr')

    def is_visible_to(self, user):
        return audience_allows(self.audience, self.audience_groups, user)

    def visible_items(self, user=None):
        """Rows this user may see. Falls back to the plain visibility flag when
        no user is given, so existing callers keep working."""
        if user is None:
            return [i for i in self.items.all() if i.is_visible]
        return [i for i in self.items.all() if i.is_visible_to(user)]

    def columns_for(self, user):
        """This row's children grouped into columns, filtered and ordered.

        Returns a list of {'index': n, 'blocks': [...]} so a template can walk
        the row without doing arithmetic. Empty columns are still returned, or
        the editor would have nowhere to drop a block.
        """
        live = [c for c in self.children.all()
                if c.is_visible and c.deleted_at is None
                and c.schedule_state == 'live' and c.is_visible_to(user)]
        cols = []
        for i in range(self.column_count):
            blocks = sorted((c for c in live if c.column == i),
                            key=lambda c: (not c.pinned, c.order, c.pk))
            cols.append({'index': i, 'blocks': blocks})
        return cols


class SectionItem(_PictureMixin):
    """A row inside a section — a card, link, stat, file, photo, FAQ entry…

    One flexible row type keeps the section library open-ended: a new template
    can combine these fields differently without new models or migrations.
    """
    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='items')
    order   = models.IntegerField(default=0)
    is_visible = models.BooleanField(default=True)

    title = models.CharField(max_length=250, blank=True)
    text  = models.TextField(blank=True)
    meta  = models.CharField(max_length=120, blank=True,
                             help_text='Small secondary line — date, role, size…')
    badge = models.CharField(max_length=60, blank=True)

    icon  = models.CharField(max_length=40, blank=True)
    image = models.ImageField(upload_to='cms/items/', blank=True, null=True)
    file  = models.FileField(upload_to='cms/files/', blank=True, null=True)

    url          = models.CharField(max_length=500, blank=True)
    button_label = models.CharField(max_length=80, blank=True)
    button_icon  = models.CharField(max_length=40, blank=True)

    #: Style hint for items rendered as buttons — the primary actions in a page
    #: header, for instance, where one action leads and the rest are secondary.
    variant = models.CharField(
        max_length=10, blank=True, default='',
        choices=[('', 'Default'), ('primary', 'Primary'), ('ghost', 'Secondary')])

    # Rows need their own visibility, not just their section's: a page header
    # holds several actions and each is for a different audience — "New article"
    # for HR, "Manage brand files" for Branding. Without this those buttons had
    # to stay hardcoded in a template behind an {% if %}.
    # An item can point at a real colleague. A "meet the team" card built from
    # typed-in text goes stale the moment someone changes role, and it cannot
    # offer a working Message link because there is no user behind it. Optional,
    # so the same block can still hold a card for someone outside the Hub.
    person = models.ForeignKey(
        'people.Employee', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='cms_items',
        help_text='Link a colleague to fill the name, role, photo and contacts.')

    audience = models.CharField(max_length=20, choices=Audience.choices,
                                default=Audience.EVERYONE)
    audience_groups = models.ManyToManyField(
        Group, blank=True, related_name='cms_items',
        help_text='Used when the audience is "Specific groups".')

    class Meta:
        ordering = ['order', 'pk']

    def __str__(self):
        return self.title or f'Item {self.pk}'

    def is_visible_to(self, user):
        return self.is_visible and audience_allows(self.audience,
                                                   self.audience_groups, user)

    @property
    def href(self):
        """Prefer an uploaded file, else the typed URL."""
        if self.file:
            return self.file.url
        return self.url

    @property
    def filesize(self):
        try:
            kb = self.file.size / 1024
        except Exception:
            return ''
        return f'{kb/1024:.1f} MB' if kb >= 1024 else f'{kb:.0f} KB'


# ── editing rights ────────────────────────────────────────────────────────
def user_can_edit_cms(user):
    """May this user manage Hub pages?

    Deliberately broad so the concrete roles can be decided later: superusers,
    anyone holding the Django model permission, and anyone in the
    "Content Editors" group all qualify.
    """
    if not user or not user.is_authenticated:
        return False
    return (
        user.is_superuser
        or user.has_perm('cms.change_page')
        or user.groups.filter(name=CONTENT_EDITORS_GROUP).exists()
    )


def user_can_publish_cms(user):
    if not user or not user.is_authenticated:
        return False
    return user.is_superuser or user.has_perm('cms.publish_page')


def user_can_manage_icons(user):
    """Icons appear in navigation and buttons, so this is deliberately the same
    audience as page editing rather than a wider one."""
    return user_can_edit_cms(user)


# Imported last so Django discovers the Icon model through this module, keeping
# the app's models importable from one place.
from .icon_models import (Icon, IconCategory, icon_map,  # noqa: E402,F401
                          render_icon, sanitise_svg)


# ── Site chrome: navigation and the logo ─────────────────────────────────────
# The navigation was a hardcoded array in base.html — labels, links, icons and
# the dropdown groupings all compiled in — so renaming "Policies & SOPs" or
# adding a link meant editing a template. These make it records like everything
# else, with the same audience rules pages and blocks already use.

class NavItem(models.Model):
    """One entry in the top navigation, or one link inside a dropdown.

    A child is an item with a parent. A top-level item either goes somewhere
    itself or opens a dropdown of children; it never needs both, and the
    renderer prefers children when there are any.
    """
    label = models.CharField(max_length=60)
    url = models.CharField(
        max_length=300, blank=True,
        help_text='A Hub route like /policies/, or a full address for an '
                  'external site. Leave empty on an item that only opens a menu.')
    icon = models.CharField(max_length=40, blank=True,
                            help_text='Shown beside a dropdown link.')
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True,
                               related_name='children')
    order = models.IntegerField(default=0)
    is_visible = models.BooleanField(default=True)

    #: Same audience vocabulary as pages and blocks, so a link can be hidden
    #: from most staff without a second permissions concept to learn.
    audience = models.CharField(max_length=20, choices=Audience.choices,
                                default=Audience.EVERYONE)
    audience_groups = models.ManyToManyField('auth.Group', blank=True,
                                             related_name='nav_items')

    #: Opens in a new tab. Set for anything that leaves the Hub, so somebody
    #: following a link to an external system does not lose their place.
    new_tab = models.BooleanField(default=False)

    class Meta:
        ordering = ['order', 'pk']
        verbose_name = 'navigation item'

    def __str__(self):
        return self.label

    def clean(self):
        from django.core.exceptions import ValidationError
        # One level of dropdown. A third would have nowhere to render and the
        # menu would silently lose the deepest links.
        if self.parent_id and self.parent.parent_id:
            raise ValidationError('Navigation is one level of dropdown deep.')
        if self.parent_id and self.parent_id == self.pk:
            raise ValidationError('An item cannot sit inside itself.')

    #: Links whose visibility a module decides for itself, keyed by where they
    #: point. A menu entry leading to a page that then refuses you is worse than
    #: no entry, so the module's own rule is asked here too rather than being
    #: mirrored into this item's audience by hand and drifting from it.
    MODULE_GATES = {
        '/brand/': 'portal.models.brand_files_visible_to',
    }

    def _module_allows(self, user):
        gate = self.MODULE_GATES.get((self.url or '').rstrip('/') + '/')
        if not gate:
            return True
        from importlib import import_module
        module_path, _, attr = gate.rpartition('.')
        try:
            return getattr(import_module(module_path), attr)(user)
        except Exception:
            # A gate that cannot be loaded must not take the whole menu with it.
            return True

    def is_visible_to(self, user):
        if not self.is_visible:
            return False
        if not self._module_allows(user):
            return False
        return audience_allows(self.audience, self.audience_groups.all(), user)


class SiteBranding(models.Model):
    """The logo and site name. One row; the helper below keeps it that way."""
    logo = models.ImageField(upload_to='branding/', blank=True, null=True,
                             help_text='Shown top left. A wide image works best.')
    favicon = models.ImageField(upload_to='branding/', blank=True, null=True,
                                help_text='The small icon in the browser tab.')
    site_name = models.CharField(max_length=80, default='AJi Hub',
                                 help_text='Used in the tab title and read out '
                                           'by screen readers on the logo.')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'site branding'
        verbose_name_plural = 'site branding'

    def __str__(self):
        return self.site_name

    #: Colours in an uploaded logo that are the mark rather than the ink, and are
    #: left alone when the dark version is made. The brand yellow is the only one
    #: so far; anything else near-black becomes white and anything near-white
    #: becomes transparent.
    _LOGO_KEEP = ((252, 215, 0),)

    @property
    def logo_dark_url(self):
        """The uploaded logo with its ink turned white, for the dark theme.

        A wordmark is almost always black on transparency, which disappears
        against a dark top bar — on every page, since the bar is on every page.
        The bundled mark ships with a hand-made pair (logo.png / logo-dark.png);
        an uploaded one has no such counterpart, so one is derived here.

        Generated once and cached beside the original as `<name>.dark.png`, and
        regenerated whenever the original is newer. Returns the original's URL if
        anything goes wrong: a logo that is hard to read on one theme is a much
        smaller problem than a header that raises.
        """
        if not self.logo:
            return ''
        try:
            import os
            from PIL import Image
            from django.core.files.storage import default_storage

            src = self.logo.name
            dark = f'{os.path.splitext(src)[0]}.dark.png'
            if (not default_storage.exists(dark)
                    or default_storage.get_modified_time(dark)
                    < default_storage.get_modified_time(src)):
                with default_storage.open(src, 'rb') as fh:
                    im = Image.open(fh).convert('RGBA')
                out = Image.new('RGBA', im.size)
                get, put = im.load(), out.load()
                for y in range(im.size[1]):
                    for x in range(im.size[0]):
                        r, g, b, a = get[x, y]
                        if a == 0:
                            put[x, y] = (0, 0, 0, 0)
                        elif any(abs(r - kr) < 42 and abs(g - kg) < 46 and abs(b - kb) < 60
                                 for kr, kg, kb in self._LOGO_KEEP):
                            put[x, y] = (r, g, b, a)          # the mark's own colour
                        elif min(r, g, b) > 200:
                            put[x, y] = (0, 0, 0, 0)          # white fill -> nothing
                        else:
                            put[x, y] = (255, 255, 255, a)    # the ink -> white
                from io import BytesIO
                buf = BytesIO()
                out.save(buf, 'PNG')
                if default_storage.exists(dark):
                    default_storage.delete(dark)
                from django.core.files.base import ContentFile
                default_storage.save(dark, ContentFile(buf.getvalue()))
            return default_storage.url(dark)
        except Exception:
            return self.logo.url

    @classmethod
    def get(cls):
        """The single row, created on first use.

        Returns an unsaved instance if the table is not there yet, so a page can
        still render before migrations have run.
        """
        try:
            row, _ = cls.objects.get_or_create(pk=1)
            return row
        except Exception:
            return cls()


def nav_tree(user):
    """The navigation this reader may see, as [(item, [children])].

    A dropdown whose every child is hidden from this reader would otherwise
    render as a menu that opens onto nothing, so it is dropped too — unless the
    parent is a link in its own right.
    """
    items = (NavItem.objects.filter(parent__isnull=True)
             .prefetch_related('children', 'audience_groups',
                               'children__audience_groups'))
    out = []
    for item in items:
        if not item.is_visible_to(user):
            continue
        kids = [k for k in item.children.all() if k.is_visible_to(user)]
        if not kids and not item.url:
            continue
        out.append((item, kids))
    return out


def copy_sections(source, target):
    """Copy every block from one page to another, nesting and items included.

    Two things make this more than a loop. A block inside a columns row points at
    its row by primary key, so the parents have to be remapped as they are
    created — without that, a copied block would still point at the *original*
    row and appear on the page it was copied from. And a soft-deleted block is
    left behind deliberately: a draft starts from what is on the page, not from
    its undo history.
    """
    remap = {}

    # Parents before children, so a child always finds its new parent already
    # made. Ordering by parent_id puts the top-level blocks (NULL) first.
    blocks = list(Section.objects.filter(page=source)
                  .order_by('parent_id', 'order', 'pk'))
    for block in blocks:
        old_pk = block.pk
        groups = list(block.audience_groups.all())
        items = list(block.items.all())

        block.pk = None
        block.id = None
        block.page = target
        block.parent_id = remap.get(block.parent_id) if block.parent_id else None
        # Remember the block this was copied from, so publishing can update it
        # in place instead of deleting the page and rebuilding it.
        block.origin_id = old_pk
        block.save()
        remap[old_pk] = block.pk
        if groups:
            block.audience_groups.set(groups)

        for item in items:
            item.pk = None
            item.id = None
            item.section = block
            item.save()

    return remap


#: Columns the merge must NOT copy, because they are what identifies a block
#: rather than what it says: which page it is on, where it sits in the tree, and
#: which live block a draft copy came from.
MERGE_SKIP = {'id', 'page', 'parent', 'origin'}
#: Their attname forms, since concrete_fields reports page_id and so on.
MERGE_SKIP_ATTNAMES = {'id', 'page_id', 'parent_id', 'origin_id'}


def _merge_fields():
    """Every concrete column a draft can change, read from the model.

    Listed by hand at first, which silently dropped `person` the moment that
    field was added — and would have dropped the next one too. Asking the model
    means a new field is copied on publish without anyone remembering to add it
    here.
    """
    return [f.attname for f in Section._meta.concrete_fields
            if f.name not in MERGE_SKIP and f.attname not in MERGE_SKIP_ATTNAMES]


def merge_sections(draft, live):
    """Apply a draft's blocks to the live page, keeping identities.

    The obvious way to publish is to delete the live page's blocks and copy the
    draft's over. That works, but it gives every block a new primary key on every
    publish — so a link to a block dies, the removed-blocks recovery list is
    wiped, and anything holding an id silently points at nothing.

    So instead: a draft block that came from a live block updates that block, a
    draft block with no origin is genuinely new, and a live block that no draft
    block points at was deleted while drafting and goes.
    """
    fields = _merge_fields()
    draft_blocks = list(Section.all_objects.filter(page=draft)
                        .order_by('parent_id', 'order', 'pk'))
    live_by_pk = {b.pk: b for b in Section.all_objects.filter(page=live)}

    # draft pk -> the live pk it ends up as, for remapping parents.
    resolved = {}
    kept = set()

    for d in draft_blocks:
        target = live_by_pk.get(d.origin_id) if d.origin_id else None
        if target is None:
            target = Section(page=live)

        for field in fields:
            setattr(target, field, getattr(d, field))
        target.page = live
        target.origin = None
        # The parent is a *draft* block, so it has to become whichever live
        # block that draft block resolved to. Parents come first in the ordering
        # above, so the answer is always already known.
        target.parent_id = resolved.get(d.parent_id) if d.parent_id else None
        target.save()

        resolved[d.pk] = target.pk
        kept.add(target.pk)
        target.audience_groups.set(d.audience_groups.all())

        # Items have no identity worth preserving across a publish -- nothing
        # links to one -- so they are replaced wholesale.
        target.items.all().delete()
        for item in d.items.all():
            item.pk = None
            item.id = None
            item.section = target
            item.save()

    # Whatever the draft no longer has was removed while drafting.
    Section.all_objects.filter(page=live).exclude(pk__in=kept).delete()
    return resolved
