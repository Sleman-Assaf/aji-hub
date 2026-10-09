# portal/models.py
from django.core.validators import FileExtensionValidator
from django.db import models
from django.contrib.auth.models import Group, User


class DocAccess(models.TextChoices):
    """Who may open a document library or a document in it."""
    EVERYONE = 'everyone', 'Everyone signed in'
    RESTRICTED = 'restricted', 'Chosen groups and people only'


class LibraryRole(models.TextChoices):
    """What one person may do with the documents in a library.

    Being let into a library and being allowed to carry its documents away are
    two different permissions, and there was only one control for both: the
    library's own `downloadable`, which applied to everybody at once. So a
    library shared with an auditor who should only read it could not also be
    shared with the team who need to fill its forms in.
    """
    READ = 'read', 'Read in the Hub only'
    DOWNLOAD = 'download', 'Read and download'


class LibraryAccess(models.TextChoices):
    """The three answers a document library can give, and only a library.

    Deliberately its own set rather than a third member of DocAccess. DocAccess
    is shared with Brand Files and with individual documents, so adding a value
    there offered "by request" on four models that have nothing to answer a
    request with — a setting somebody could choose and that would then do
    nothing. Every one of those fields showed up in the migration as changed,
    which is what gave it away.

    The middle answer is the reason there are three. A library can be open to
    the company; it can be closed and unlisted, so that people who are not in it
    never learn it exists; or it can be closed but *listed*, which is how a team
    says "this is ours, ask us if you need it". Without it, the only way to let
    somebody discover a library was to give them everything in it.

    REQUEST is about discovery, not permission: it changes who sees the tile,
    never who may read what is behind it. Approving a request adds the person to
    `allowed_users`, so from then on the ordinary restricted rule is what lets
    them in — there is only ever one rule deciding access.
    """
    EVERYONE = 'everyone', 'Everyone signed in'
    REQUEST = 'request', 'Listed to everyone — contents by request'
    RESTRICTED = 'restricted', 'Chosen people only — not listed'


#: What a document says when it defers to the library it sits in.
INHERIT = 'inherit'


def _access_allows(access, groups_qs, users_qs, user):
    """The one rule, used by libraries and by documents.

    An Intranet Admin always passes: somebody has to be able to fix a library
    that has been restricted to a group nobody is in any more.
    """
    if not user or not user.is_authenticated:
        return False
    if access == DocAccess.EVERYONE:
        return True
    from core.models import is_intranet_admin
    if is_intranet_admin(user):
        return True
    if users_qs.filter(pk=user.pk).exists():
        return True
    return groups_qs.filter(pk__in=user.groups.values('pk')).exists()


class PolicyCategory(models.Model):
    """Top-level clickable category tiles on the Policies & SOPs page."""
    name        = models.CharField(max_length=200)
    description = models.CharField(max_length=300, blank=True, help_text='Short subtitle shown on the tile')
    #: The mark on the tile. Every category used to draw the same hardcoded
    #: shield, so a page of them said only "these are all policies" — which the
    #: heading above them already said. Picked from the icon library at
    #: /policies/manage/; blank falls back to the shield it always was, so
    #: nothing looks broken before anybody has chosen anything.
    icon        = models.CharField(max_length=60, blank=True,
                                   help_text='Icon name from the library. Blank uses a shield.')
    order       = models.PositiveIntegerField(default=0)

    # ── Who may open this library, and what they may do with what is in it ──
    access = models.CharField(max_length=12, choices=LibraryAccess.choices,
                              default=LibraryAccess.EVERYONE,
                              help_text='Open to everyone, listed but by request, or closed and unlisted.')
    allowed_groups = models.ManyToManyField(
        Group, blank=True, related_name='policy_libraries',
        help_text='Used when access is restricted.')
    #: Named people as well as groups, because the group that should see a
    #: document does not always exist — and creating a group of one to share one
    #: file is how group lists stop meaning anything.
    allowed_users = models.ManyToManyField(
        User, blank=True, related_name='policy_libraries',
        through='LibraryMember',
        help_text='Individuals, on top of any groups above.')
    downloadable = models.BooleanField(
        default=True,
        help_text='Off means documents can be read in the Hub but not saved or printed from it.')

    #: Who owns this library. Everybody in the Policies group can create one,
    #: and a library is often for a group of people from several divisions who
    #: have nothing else in common — so its settings belong to whoever set it up
    #: rather than to everyone who happens to hold the same role. Null on the
    #: libraries that existed before this field did; those stay editable by the
    #: Policies group so nobody is locked out of their own work.
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='policy_libraries_owned')
    #: The people the owner has asked to help run it. Running a library is a job
    #: that goes on leave: without deputies, a library whose owner is away cannot
    #: take a new document or answer a request, and the only way round it was to
    #: hand somebody the whole Policies group. Managing is not reading — a
    #: manager is added here *and* keeps whatever read access they already had.
    managers = models.ManyToManyField(
        User, blank=True, related_name='policy_libraries_managed',
        help_text='They can add documents and folders, and answer access '
                  'requests, alongside the owner.')
    created_at = models.DateTimeField(auto_now_add=True, null=True)

    class Meta:
        ordering = ['order', 'name']
        verbose_name_plural = 'Policy Categories'

    def __str__(self):
        return self.name

    def allows(self, user):
        """Whether this person may read what is in the library.

        REQUEST is deliberately not special here. A library awaiting somebody's
        request is closed to them, and the moment it is approved they are in
        `allowed_users` and the restricted rule lets them in — so "may read" has
        one implementation and cannot drift from "is listed".
        """
        access = (DocAccess.RESTRICTED if self.access == LibraryAccess.REQUEST
                  else self.access)
        return _access_allows(access, self.allowed_groups,
                              self.allowed_users, user)

    def visible_to(self, user):
        """Whether this person should see the library exists.

        The same as `allows` except for a request-based library, which is listed
        to everyone signed in precisely so it can be asked for. This is what the
        index filters on; `allows` is what guards the contents. Keeping them
        apart is the whole feature: a tile somebody can see but not open.
        """
        if self.access == LibraryAccess.REQUEST:
            return bool(user and user.is_authenticated)
        return self.allows(user)

    def request_state(self, user):
        """'' | 'open' | 'pending' | 'declined' — what to draw on the tile.

        Only meaningful for a request-based library this person cannot yet read.

        An approved row is deliberately *not* treated as access. `allows` is the
        gate: approving adds the person to allowed_users, and taking them off
        that list again is how access is revoked. So a row still marked approved
        while `allows` says no means the access was withdrawn afterwards, and the
        row is history rather than permission. Reading the status instead drew a
        tile that looked open, opened nothing, and offered no way to ask again —
        and two people were in exactly that state.
        """
        if self.access != LibraryAccess.REQUEST or self.allows(user):
            return ''
        if not user or not user.is_authenticated:
            return ''
        row = self.access_requests.filter(user=user).first()
        if row is None or row.status == PolicyAccessRequest.Status.APPROVED:
            return 'open'
        return row.status

    def can_manage(self, user):
        """Whether this person may change the library's settings or delete it.

        Seeing a library and running it are different things: a colleague in the
        same group may well be allowed to read what is in here without being
        allowed to change who else can.
        """
        if not user or not user.is_authenticated:
            return False
        from core.models import is_intranet_admin, user_can_manage_policies
        if is_intranet_admin(user):
            return True
        if self.managers.filter(pk=user.pk).exists():
            return True
        if self.created_by_id:
            return self.created_by_id == user.pk
        # No owner recorded — a library from before ownership existed. Left to
        # the Policies group rather than orphaned.
        return user_can_manage_policies(user)

    def role_for(self, user):
        """LibraryRole — what this person may do with what is in here.

        The owner and the deputies always get the full level: they can add and
        remove the documents, so withholding a copy of one would be theatre. So
        does anybody let in by a group — a role is recorded against a named
        person and there is nowhere to record one for a group, and inventing
        read-only as the default for groups would quietly take downloads away
        from everybody who has them today.
        """
        if not user or not getattr(user, 'is_authenticated', False):
            return LibraryRole.READ
        if self.created_by_id == user.pk:
            return LibraryRole.DOWNLOAD
        if self.managers.filter(pk=user.pk).exists():
            return LibraryRole.DOWNLOAD
        row = self.memberships.filter(user=user).first()
        return row.role if row else LibraryRole.DOWNLOAD

    def manager_ids(self):
        """The owner and the deputies, for notifying whoever has to answer."""
        ids = set(self.managers.values_list('pk', flat=True))
        if self.created_by_id:
            ids.add(self.created_by_id)
        return ids

    @property
    def is_restricted(self):
        return self.access == DocAccess.RESTRICTED

    @property
    def owner_name(self):
        if not self.created_by:
            return ''
        return self.created_by.get_full_name() or self.created_by.username


class PolicyAccessRequest(models.Model):
    """Somebody asking to be let into a request-based library.

    One row per person per library, reused rather than piled up: asking twice
    should not put two entries in front of whoever has to answer. Modelled on
    exchange.JoinRequest deliberately — asking to be let in to something is the
    same act whether the something is a community or a document library, and two
    different shapes of the same idea is how two different answers to "have I
    already asked?" get written.
    """
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        DECLINED = 'declined', 'Declined'

    library = models.ForeignKey('PolicyCategory', on_delete=models.CASCADE,
                                related_name='access_requests')
    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='policy_access_requests')
    #: Why they want in. Optional, but it is what lets an owner answer without
    #: having to go and ask.
    message = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                    blank=True,
                                    related_name='policy_requests_resolved')

    class Meta:
        unique_together = [('library', 'user')]
        ordering = ['-created_at']

    def __str__(self):
        who = self.user.get_full_name() or self.user.username
        return f'{who} \u2192 {self.library}'


class LibraryMember(models.Model):
    """One person's place in one library, and what they may do with it.

    An explicit through model for `PolicyCategory.allowed_users`, declared on the
    table the implicit one already had, so not a single existing row moves — the
    migration is state-only apart from adding `role`.

    It exists so that "who is in" and "what they may do" can be answered per
    person rather than per library. Nothing else changes: `allowed_users` is
    still the gate, and adding a name still lets that name in.
    """
    class Meta:
        db_table = 'portal_policycategory_allowed_users'
        unique_together = [('policycategory', 'user')]
        verbose_name = 'library member'

    #: Declared rather than left to DEFAULT_AUTO_FIELD. The table predates this
    #: model — Django built it for the implicit many-to-many — and its id is an
    #: INTEGER PRIMARY KEY, so a BigAutoField here would leave the project
    #: permanently wanting to migrate a column that is already right.
    id = models.AutoField(primary_key=True)
    policycategory = models.ForeignKey('PolicyCategory', on_delete=models.CASCADE,
                                       related_name='memberships')
    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='library_memberships')
    #: Defaults to the full level, because that is what everybody already had
    #: when this column did not exist. A migration that quietly took downloads
    #: away from every named person would be a permission change dressed up as a
    #: schema change.
    role = models.CharField(max_length=10, choices=LibraryRole.choices,
                            default=LibraryRole.DOWNLOAD)

    def __str__(self):
        who = self.user.get_full_name() or self.user.username
        return f'{who} in {self.policycategory} ({self.get_role_display()})'


class PolicyFolder(models.Model):
    """A folder inside a document library, nested as deeply as needed.

    A library of manuals is not a flat pile: it is chapters, and chapters have
    parts. Documents used to hang directly off the library, so the only way to
    express structure was to type it into every title.

    `parent` is what makes it a tree, and the two things a tree needs guarding
    are depth and cycles. `full_path` walks upward with a hard stop rather than
    recursing freely, because a folder that has somehow become its own ancestor
    should draw a broken breadcrumb, not hang the page.
    """
    #: How deep the tree may go. Not a technical limit — a guard against a
    #: structure nobody can navigate, and the number the move form checks.
    MAX_DEPTH = 6

    library = models.ForeignKey('PolicyCategory', on_delete=models.CASCADE,
                                related_name='folders')
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True,
                               blank=True, related_name='children')
    name = models.CharField(max_length=200)
    order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True, null=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='policy_folders_made')

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Policy folder'

    def __str__(self):
        return self.name

    # ── the tree ──────────────────────────────────────────────────────────

    def ancestors(self):
        """Outermost first, not including this folder. Cycle-safe."""
        out, seen, node = [], {self.pk}, self.parent
        while node is not None and node.pk not in seen and len(out) < self.MAX_DEPTH + 2:
            out.append(node)
            seen.add(node.pk)
            node = node.parent
        out.reverse()
        return out

    @property
    def depth(self):
        """0 for a folder at the top of its library."""
        return len(self.ancestors())

    @property
    def full_path(self):
        return ' / '.join([f.name for f in self.ancestors()] + [self.name])

    def descendant_ids(self):
        """This folder and everything under it, breadth-first.

        Used to stop a folder being moved inside itself, and to count what a
        deletion would take with it.
        """
        found = [self.pk]
        frontier = [self.pk]
        guard = 0
        while frontier and guard < 64:
            guard += 1
            frontier = list(PolicyFolder.objects
                            .filter(parent_id__in=frontier)
                            .exclude(pk__in=found)
                            .values_list('pk', flat=True))
            found.extend(frontier)
        return found

    def may_move_to(self, parent):
        """Whether this folder can become a child of `parent`.

        Three ways it cannot: into itself, into its own descendant — which would
        cut the branch off the tree entirely and leave it unreachable — or so
        deep that the breadcrumb stops being readable.
        """
        if parent is None:
            return True
        if parent.pk == self.pk:
            return False
        if parent.library_id != self.library_id:
            return False
        if parent.pk in self.descendant_ids():
            return False
        return parent.depth + 1 <= self.MAX_DEPTH


class Policy(models.Model):
    class DocType(models.TextChoices):
        """What kind of controlled document this is.

        One model rather than two: policies and SOPs share categories, listing,
        search and permissions, and only needed telling apart. Before this an SOP
        was a policy with "SOP" typed into its title, so the Policies & SOPs page
        could not filter or report on them separately.
        """
        POLICY    = 'policy',    'Policy'
        SOP       = 'sop',       'SOP'
        GUIDELINE = 'guideline', 'Guideline'
        FORM      = 'form',      'Form / template'

    doc_type    = models.CharField(max_length=12, choices=DocType.choices,
                                   default=DocType.POLICY, db_index=True)
    title       = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    category    = models.ForeignKey(PolicyCategory, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='policies')
    #: Which folder inside that library, if any. Null means it sits at the top
    #: of the library, which is where everything was before folders existed —
    #: so nothing had to be filed for the change to land.
    folder      = models.ForeignKey('PolicyFolder', on_delete=models.SET_NULL,
                                    null=True, blank=True,
                                    related_name='documents')
    file        = models.FileField(upload_to='policies/', blank=True, null=True)
    external_url = models.URLField(blank=True, help_text='Link to an external document (SharePoint, etc.)')
    published_date = models.DateField(null=True, blank=True)
    updated_date   = models.DateField(null=True, blank=True)
    order          = models.PositiveIntegerField(default=0)

    # ── Access, deferring to the library unless this document says otherwise ──
    # The default is `inherit` so that filing a document somewhere is enough to
    # secure it. An override exists for the one confidential paper that has to
    # live in an otherwise open library.
    ACCESS_CHOICES = [(INHERIT, 'Same as the library')] + list(DocAccess.choices)
    access = models.CharField(max_length=12, choices=ACCESS_CHOICES, default=INHERIT)
    allowed_groups = models.ManyToManyField(Group, blank=True, related_name='policies',
                                            help_text='Used when this document sets its own access.')
    allowed_users = models.ManyToManyField(User, blank=True, related_name='policies',
                                           help_text='Individuals, on top of any groups above.')
    #: Three states, which is why it is nullable rather than a boolean: null
    #: means "whatever the library says", and the library is where this is
    #: normally decided.
    downloadable = models.BooleanField(
        null=True, blank=True,
        help_text='Leave unset to follow the library. Off means read in the Hub only.')

    class Meta:
        ordering = ['order', 'title']
        verbose_name_plural = 'Policies'

    def __str__(self):
        return self.title

    # ── Access ────────────────────────────────────────────────────────────

    def allows(self, user):
        """Whether this person may open this document at all.

        A document in a library they cannot open is never visible, even if it
        names them: the library is the outer gate, and an override widens who
        may see a document *within* what the library already permits. Otherwise
        "restrict this library" would not mean anything — any document inside it
        could quietly reopen it.
        """
        category = self.category
        if category is not None and not category.allows(user):
            return False
        if self.access == INHERIT:
            return True if category is None else category.allows(user)
        return _access_allows(self.access, self.allowed_groups,
                              self.allowed_users, user)

    def may_download(self, user):
        """Whether they may take a copy, as opposed to reading it here.

        The person's own level is checked before anything the document or the
        library says. Somebody admitted read-only is read-only throughout: a
        single document marked downloadable is the owner saying "this one may
        leave the Hub", not "this one may leave it for everybody".
        """
        if not self.allows(user):
            return False
        if (self.category is not None
                and self.category.role_for(user) == LibraryRole.READ):
            return False
        if self.downloadable is not None:
            return self.downloadable
        return self.category.downloadable if self.category else True

    @property
    def inherits_access(self):
        return self.access == INHERIT

    @property
    def filename(self):
        """Just the name, for showing what is already attached.

        `file.name` is the storage path — "policies/QHSE_Policy_2026.pdf" — and
        putting that in a form field tells an editor about a directory layout
        they have no use for.
        """
        return self.file.name.rsplit('/', 1)[-1] if self.file else ''


class BrandVisibility(models.Model):
    """Who may reach Brand Files at all. One row.

    Sections and tiles carry their own access below, but somebody has to be able
    to say "this whole area is not for everyone" in one place — and the answer
    also decides whether Brand Files appears in the navigation bar, because a
    menu entry leading to a page that refuses you is worse than no entry.
    """
    access = models.CharField(max_length=12, choices=DocAccess.choices,
                              default=DocAccess.EVERYONE,
                              help_text='Everyone signed in, or only chosen groups and people.')
    allowed_groups = models.ManyToManyField(
        Group, blank=True, related_name='brand_visibility',
        help_text='Used when access is restricted.')
    allowed_users = models.ManyToManyField(
        User, blank=True, related_name='brand_visibility',
        help_text='Individuals, on top of any groups above.')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Brand Files visibility'
        verbose_name_plural = 'Brand Files visibility'

    def __str__(self):
        return f'Brand Files: {self.get_access_display()}'

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def allows(self, user):
        # Whoever manages the brand can always reach it, or restricting it to a
        # group they are not in would lock them out of their own screens.
        from core.models import user_can_manage_branding
        if user_can_manage_branding(user):
            return True
        return _access_allows(self.access, self.allowed_groups,
                              self.allowed_users, user)


def brand_files_visible_to(user):
    """Whether this person may reach Brand Files. Also used by the navigation."""
    return BrandVisibility.get().allows(user)


class BrandSection(models.Model):
    """
    Top-level section on the Brand Files page.
    e.g. "Templates", "Brand Assets", "Logos"
    """
    SECTION_TYPES = [
        ('templates',    'Templates'),
        ('brand_assets', 'Brand Assets'),
        ('other',        'Other'),
    ]
    name         = models.CharField(max_length=200)
    section_type = models.CharField(max_length=30, choices=SECTION_TYPES, default='other')
    order        = models.PositiveIntegerField(default=0)
    #: Same vocabulary as a policy library: everyone, or chosen groups and
    #: people. A section nobody may see is simply not drawn.
    access = models.CharField(max_length=12, choices=DocAccess.choices,
                              default=DocAccess.EVERYONE,
                              help_text='Who can see this section on Brand Files.')
    allowed_groups = models.ManyToManyField(Group, blank=True,
                                            related_name='brand_sections',
                                            help_text='Used when access is restricted.')
    allowed_users = models.ManyToManyField(User, blank=True,
                                           related_name='brand_sections',
                                           help_text='Individuals, on top of any groups above.')

    class Meta:
        ordering = ['order', 'name']
        verbose_name_plural = 'Brand Sections'

    def __str__(self):
        return self.name

    def allows(self, user):
        from core.models import user_can_manage_branding
        if user_can_manage_branding(user):
            return True
        return _access_allows(self.access, self.allowed_groups,
                              self.allowed_users, user)


class BrandTemplate(models.Model):
    """
    A single brand asset tile (e.g. "Presentation", "Report Template").
    Each tile can hold multiple downloadable files (BrandTemplateFile).
    """
    section     = models.ForeignKey(BrandSection, on_delete=models.CASCADE,
                                    related_name='templates')
    name        = models.CharField(max_length=300)
    description = models.CharField(max_length=300, blank=True,
                                   help_text='Short file-type label shown on the tile, e.g. "PPTX · 16:9"')
    #: The mark on the tile, picked from the icon library at /brand/manage/.
    #: Blank keeps the default each grid has always used — a document for the
    #: templates, a book for the brand assets — so nothing changes appearance
    #: until somebody chooses.
    icon        = models.CharField(max_length=60, blank=True,
                                   help_text='Icon name from the library. Blank uses the section default.')
    preview     = models.ImageField(upload_to='brand/previews/', blank=True, null=True,
                                    help_text='Optional thumbnail shown on hover')
    order       = models.PositiveIntegerField(default=0)
    #: Three states, like a policy: follow the section, open to everyone, or a
    #: list of its own. Defaults to following, which is what a tile added to a
    #: restricted section should do without anybody having to remember.
    ACCESS_CHOICES = [(INHERIT, 'Same as the section')] + list(DocAccess.choices)
    access = models.CharField(max_length=12, choices=ACCESS_CHOICES, default=INHERIT,
                              help_text='Who can see this tile.')
    allowed_groups = models.ManyToManyField(Group, blank=True,
                                            related_name='brand_templates',
                                            help_text='Used when this tile sets its own access.')
    allowed_users = models.ManyToManyField(User, blank=True,
                                           related_name='brand_templates',
                                           help_text='Individuals, on top of any groups above.')

    class Meta:
        ordering = ['order', 'name']
        verbose_name_plural = 'Brand Templates'

    def __str__(self):
        return f'{self.section.name} / {self.name}'

    def allows(self, user):
        """Whether this person may see this tile.

        The section is the outer gate: a tile inside a section somebody cannot
        see stays hidden even when it names them. An override widens who may see
        a tile *within* what the section already permits, exactly as a policy
        behaves inside its library — otherwise restricting a section would mean
        nothing, since any tile in it could quietly reopen it.
        """
        from core.models import user_can_manage_branding
        if user_can_manage_branding(user):
            return True
        if not self.section.allows(user):
            return False
        if self.access == INHERIT:
            return True
        return _access_allows(self.access, self.allowed_groups,
                              self.allowed_users, user)

    def file_count(self):
        return self.files.count()

    def file_list(self):
        """Return list of dicts for the files modal."""
        return list(self.files.values('id', 'label', 'file', 'version', 'updated_at'))


def _brand_file_path(instance, filename):
    """Uploads to media/brand/<section>/<template>/[<group>/]<filename>"""
    import os
    section = instance.template.section.name.replace(' ', '_').lower()
    tmpl    = instance.template.name.replace(' ', '_').lower()
    if instance.group_id:
        grp = instance.group.name.replace(' ', '_').lower()
        return f'brand/{section}/{tmpl}/{grp}/{filename}'
    return f'brand/{section}/{tmpl}/{filename}'


    @property
    def ungrouped_files(self):
        """The files that are not in any group, for the tile's own list.

        A property rather than a filter in the template, because the template has
        no way to ask this: it looped every file and skipped the grouped ones with
        an {% if %}, then opened its wrapper on `forloop.first` — which is only
        true of the first file overall, grouped or not. The wrapper therefore
        opened only when the first file happened to be ungrouped, while the
        matching </div> was written under `{% if tmpl.files.all %}`, which is a
        different question. Any tile whose files were all grouped closed a div it
        had never opened, and one stray </div> in a page of nested boxes closes
        the section early and lands everything after it at the wrong depth.
        """
        return self.files.filter(group__isnull=True)


class BrandFileGroup(models.Model):
    """
    Optional subdirectory/group inside a BrandTemplate, used purely for
    arranging files (e.g. 'Light variants', 'Print-ready', 'Social sizes').
    """
    template = models.ForeignKey(BrandTemplate, on_delete=models.CASCADE,
                                  related_name='groups')
    name     = models.CharField(max_length=200)
    order    = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'name']
        verbose_name_plural = 'Brand File Groups'

    def __str__(self):
        return f'{self.template.name} / {self.name}'


class BrandTemplateFile(models.Model):
    """
    One downloadable file belonging to a BrandTemplate.
    A template can have many files (e.g. AJi Logo has SVG, PNG, dark, light variants).
    Files can optionally belong to a BrandFileGroup for arrangement.
    """
    template   = models.ForeignKey(BrandTemplate, on_delete=models.CASCADE,
                                   related_name='files')
    group      = models.ForeignKey(BrandFileGroup, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='files',
                                   help_text='Optional subgroup for arranging files')
    label      = models.CharField(max_length=300,
                                  help_text='Display name, e.g. "AJi-Logo-Primary.svg"')
    file       = models.FileField(upload_to=_brand_file_path,
                                  help_text='The actual downloadable file')
    version    = models.CharField(max_length=50, blank=True,
                                  help_text='e.g. "v2.1" or "Jun 2025"')
    updated_at = models.DateField(auto_now=True)

    class Meta:
        ordering = ['group__order', 'label']
        verbose_name_plural = 'Brand Template Files'

    def __str__(self):
        return f'{self.template.name} — {self.label}'

    def filename(self):
        import os
        return os.path.basename(self.file.name) if self.file else self.label

    def ext(self):
        import os
        return os.path.splitext(self.filename())[1].lstrip('.').upper()


class BrandColour(models.Model):
    """Colour palette entry on the Brand Files page."""
    name  = models.CharField(max_length=100)
    hex   = models.CharField(max_length=7, help_text='e.g. #FCD700')
    ref   = models.CharField(max_length=100, blank=True, help_text='e.g. Pantone 012 C or C0 M0 Y0 K100')
    light = models.BooleanField(default=False, help_text='If true, adds border to the swatch bar')
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'name']
        verbose_name_plural = 'Brand Colours'

    def __str__(self):
        return f'{self.name} ({self.hex})'


def _booth_cover_path(instance, filename):
    import os
    slug = instance.name.replace(' ', '_').lower()[:30]
    return f'brand/booth/{slug}/{filename}'


class BoothAlbum(models.Model):
    """AJi Booth photo album card."""
    TAGS = [
        ('sport',        'Sport'),
        ('company-wide', 'Company-wide'),
        ('wellness',     'Wellness'),
        ('celebration',  'Celebration'),
        ('recognition',  'Recognition'),
        ('other',        'Other'),
    ]
    name        = models.CharField(max_length=200)
    period      = models.CharField(max_length=100, blank=True, help_text='e.g. Jun 2025 or HQ Auditorium')
    tag         = models.CharField(max_length=30, choices=TAGS, default='other')
    cover_photo = models.ImageField(upload_to=_booth_cover_path, blank=True, null=True)
    photo_count = models.PositiveIntegerField(default=0)
    order       = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', '-pk']
        verbose_name_plural = 'Booth Albums'

    def __str__(self):
        return self.name

    # ── What is actually in the album ────────────────────────────────────
    # photo_count above is a number somebody types into a form. It has never
    # counted the videos, and it drifts from the files that are really there, so
    # these count the rows instead. Photos and videos are named separately rather
    # than a video being called a photo, and a count of nothing is left out —
    # "0 videos" is not information.

    @property
    def n_photos(self):
        return self.photos.count()

    @property
    def n_videos(self):
        return self.videos.count()

    @property
    def n_files(self):
        return self.n_photos + self.n_videos

    @property
    def count_parts(self):
        """['8 photos', '2 videos'] — only the kinds there are any of."""
        parts = []
        photos, videos = self.n_photos, self.n_videos
        if photos:
            parts.append(f"{photos} photo{'s' if photos != 1 else ''}")
        if videos:
            parts.append(f"{videos} video{'s' if videos != 1 else ''}")
        return parts

    @property
    def count_label(self):
        """"8 photos · 2 videos", or "Empty" when there is nothing in it."""
        return ' · '.join(self.count_parts) or 'Empty'


def _booth_photo_path(instance, filename):
    import os
    slug = instance.album.name.replace(' ', '_').lower()[:30]
    return f'brand/booth/{slug}/photos/{filename}'


class BoothPhoto(models.Model):
    """Individual photo inside a BoothAlbum."""
    album      = models.ForeignKey(BoothAlbum, on_delete=models.CASCADE, related_name='photos')
    image      = models.ImageField(upload_to=_booth_photo_path)
    caption    = models.CharField(max_length=300, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']

    def __str__(self):
        return f'{self.album.name} — photo {self.pk}'

    @property
    def is_cover(self):
        """Whether this is the photo the album card shows.

        The cover is stored as its own ImageField on the album rather than a link
        to a row here, so the two are matched by file name. Without this there was
        no way to tell which of forty photos the card was using, and the only way
        to find out was to set a new one and look.
        """
        cover = getattr(self.album, 'cover_photo', None)
        return bool(cover and self.image and cover.name == self.image.name)


def _booth_video_path(instance, filename):
    slug = instance.album.name.replace(' ', '_').lower()[:30]
    return f'brand/booth/{slug}/videos/{filename}'


class BoothVideo(models.Model):
    """Individual video inside a BoothAlbum. Upload is restricted to the
    Branding group (same as photos); viewing is open to all logged-in users."""
    ALLOWED_EXTENSIONS = ['mp4', 'webm', 'mov', 'm4v']

    album       = models.ForeignKey(BoothAlbum, on_delete=models.CASCADE, related_name='videos')
    video       = models.FileField(
        upload_to=_booth_video_path,
        validators=[FileExtensionValidator(allowed_extensions=ALLOWED_EXTENSIONS)],
    )
    caption     = models.CharField(max_length=300, blank=True)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']

    def __str__(self):
        return f'{self.album.name} — video {self.pk}'


# Keep BrandAssetCategory / BrandAsset as aliases for backwards compat
# (used in existing admin/migrations — will be cleaned up in a future migration)
class BrandAssetCategory(models.Model):
    name  = models.CharField(max_length=200)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'name']
        verbose_name_plural = 'Brand Asset Categories (legacy)'

    def __str__(self):
        return self.name


class BrandAsset(models.Model):
    category    = models.ForeignKey(BrandAssetCategory, on_delete=models.CASCADE,
                                    related_name='assets')
    name        = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    file        = models.FileField(upload_to='brand/', blank=True, null=True)
    preview     = models.ImageField(upload_to='brand/previews/', blank=True, null=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class OnboardingSection(models.Model):
    TAB_CHOICES = [
        ('conduct', 'Code of Conduct'),
        ('att', 'Attendance'),
        ('leave', 'Time Off'),
        ('prof', 'Workplace'),
        ('comp', 'Compensation'),
        ('perf', 'Performance'),
        ('exit', 'Leaving AJi'),
    ]

    tab_key = models.CharField(max_length=20, choices=TAB_CHOICES, unique=True)
    title = models.CharField(max_length=200)
    intro = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return self.get_tab_key_display()


class OnboardingItem(models.Model):
    section = models.ForeignKey(OnboardingSection, on_delete=models.CASCADE, related_name='items')
    label = models.CharField(max_length=300)
    content = models.TextField()
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.section} — {self.label}"


# ── Onboarding (HR-managed) ──────────────────────────────────────────────────

class OnboardingHandbookItem(models.Model):
    """
    A single policy/info item inside an onboarding handbook tab
    (e.g. Code of Conduct → 'Respectful Workplace').
    """
    TAB_CHOICES = [
        ('conduct', 'Code of Conduct'),
        ('att',     'Attendance'),
        ('leave',   'Time Off'),
        ('prof',    'Workplace'),
        ('comp',    'Compensation'),
        ('perf',    'Performance'),
        ('exit',    'Leaving AJi'),
    ]
    tab     = models.CharField(max_length=20, choices=TAB_CHOICES)
    title   = models.CharField(max_length=200)
    body    = models.TextField()
    order   = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['tab', 'order', 'title']
        verbose_name = 'Onboarding Handbook Item'
        verbose_name_plural = 'Onboarding Handbook Items'

    def __str__(self):
        return f'{self.get_tab_display()} — {self.title}'


# ── People & Culture (HR-managed) ────────────────────────────────────────────

class WellnessProgram(models.Model):
    """An item in the Wellness Program tab."""
    name        = models.CharField(max_length=200)
    description = models.TextField()
    period      = models.CharField(max_length=150, blank=True)
    order       = models.PositiveIntegerField(default=0)

    # Every card drew the same heart, which told a reader nothing about which
    # programme they were looking at. Chosen from the icon library, so an
    # uploaded image works here exactly like a drawn one.
    icon = models.CharField(
        max_length=40, blank=True, default='',
        help_text='Icon name from the library. Leave empty for the default heart.')

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class ChildcarePartner(models.Model):
    """A childcare partner shown in the Childcare Support tab."""
    name  = models.CharField(max_length=200)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


class ExploreTab(models.Model):
    """One tab down the side of the Explore People & Culture panel.

    These were a list of tuples in the view with their icons chosen by an
    if/elif chain in the template, so changing an icon meant editing code and
    adding a tab was impossible. As records, the label, icon and order are all
    editable, and the icon is any name from the icon library -- including an
    uploaded image.

    `key` is what ties a tab to the panel that fills it, which is why it is not
    editable once set: renaming it would leave the tab pointing at nothing.
    """
    class Group(models.TextChoices):
        """Which page's tab strip this belongs to.

        One model for both rather than two near-identical ones: the Onboarding
        handbook's tabs had their icons chosen by an if/elif chain in its template
        exactly as the People & Culture ones did, so they had the same problem and
        take the same fix.
        """
        PC         = 'pc',         'People & Culture'
        ONBOARDING = 'onboarding', 'Onboarding handbook'

    group = models.CharField(max_length=12, choices=Group.choices,
                             default=Group.PC, db_index=True)
    key   = models.SlugField(max_length=40,
                             help_text='Identifies the panel this tab opens. Do not '
                                       'change it once the tab is in use.')
    label = models.CharField(max_length=60)
    icon  = models.CharField(max_length=40, blank=True, default='heart',
                             help_text='Icon name from the library.')
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(
        default=True, help_text='Untick to hide the tab without deleting it.')

    class Meta:
        ordering = ['group', 'order', 'label']
        verbose_name = 'Explore tab'
        # Unique per strip, not globally: both strips are free to use the same key.
        constraints = [models.UniqueConstraint(fields=['group', 'key'],
                                               name='uniq_tab_key_per_group')]

    def __str__(self):
        return self.label


class GrowthCertification(models.Model):
    """A professional certification listed under My Growth."""
    name  = models.CharField(max_length=100)
    order = models.PositiveIntegerField(default=0)

    # Every chip drew the same award ribbon. Chosen from the icon library, so an
    # uploaded image works here exactly like a drawn one.
    icon = models.CharField(
        max_length=40, blank=True, default='',
        help_text='Icon name from the library. Leave empty for the default ribbon.')

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name


# ── Onboarding page assets (CEO letter + Handbook file) ──────────────────────

def _ceo_photo_path(instance, filename):
    return f'onboarding/ceo/{filename}'


class CEOLetter(models.Model):
    """
    Singleton-style record for the 'Words from our CEO' card.
    Only one active record is used (the latest one).
    """
    name   = models.CharField(max_length=200, default='Hamzah Awwad')
    title  = models.CharField(max_length=200, default='CEO · AJi Group')
    photo  = models.ImageField(upload_to=_ceo_photo_path, blank=True, null=True)
    body   = models.TextField(
        help_text='Use blank lines to separate paragraphs.',
        default='Dear New Joiner,\n\nI am delighted to extend a heartfelt welcome to you as you embark on this exciting journey with AJi Group.'
    )
    sign_off = models.CharField(max_length=200, default='Sincerely, Hamzah Awwad')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'CEO Letter'
        verbose_name_plural = 'CEO Letter'

    def __str__(self):
        return f'CEO Letter — {self.name}'

    def paragraphs(self):
        return [p.strip() for p in self.body.split('\n\n') if p.strip()]


def _handbook_file_path(instance, filename):
    return f'onboarding/handbook/{filename}'


class EmployeeHandbookFile(models.Model):
    """The downloadable Employee Handbook document."""
    title       = models.CharField(max_length=200, default='Employee Handbook')
    description = models.TextField(
        default="A full overview of AJi's culture, expectations, policies and procedures — your reference for responsibilities, rights, benefits and the standards that guide daily work."
    )
    file        = models.FileField(upload_to=_handbook_file_path, blank=True, null=True)
    version     = models.CharField(max_length=40, blank=True,
                                   help_text='Optional, e.g. "v3.1" or "2026 edition".')
    read_url    = models.CharField(
        max_length=300, blank=True, default='/onboarding/',
        help_text='Where "Open handbook" goes. Defaults to the onboarding guide.')
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Employee Handbook File'
        verbose_name_plural = 'Employee Handbook File'

    def __str__(self):
        return self.title

    @classmethod
    def current(cls):
        """The single handbook record, or None when HR hasn't set one up."""
        return cls.objects.first()

    @property
    def filesize(self):
        try:
            kb = self.file.size / 1024
        except Exception:
            return ''
        return f'{kb/1024:.1f} MB' if kb >= 1024 else f'{kb:.0f} KB'

    #: The value read_url carries when nobody has chosen a destination.
    DEFAULT_READ_URL = '/onboarding/'

    @property
    def open_url(self):
        """Where "Open handbook" should go.

        The uploaded document wins. "Open handbook" was sending people to the
        onboarding guide while an actual handbook PDF sat uploaded and unopened,
        because read_url still held its default and the template used that
        without looking at the file.

        Read here rather than stored, so re-uploading a PDF cannot leave the
        link pointing at the previous filename.
        """
        chosen = (self.read_url or '').strip()
        if chosen and chosen != self.DEFAULT_READ_URL:
            return chosen                       # HR pointed it somewhere on purpose
        if self.file:
            try:
                return self.file.url
            except Exception:
                pass
        return self.DEFAULT_READ_URL
