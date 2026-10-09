# dam/models.py
"""One document and media library, reused by every module.

The Hub had 25 separate FileField/ImageField declarations across 10 apps, each
with its own upload view and no shared notion of folders, versions, tags or
permissions. This app provides that once:

    Space  ->  Folder (nested)  ->  Asset  ->  AssetVersion

A Space is a named entry point (Brand Files, Policies & SOPs, AJi Booth …) so
those modules become views onto the same library rather than separate builds.
"""
import os

from django.contrib.auth.models import Group, User
from django.db import models
from django.utils.text import slugify

DAM_MANAGERS_GROUP = 'Document Managers'


class Audience(models.TextChoices):
    """Mirrors cms.Audience so permissions read the same across the Hub."""
    EVERYONE = 'everyone', 'Everyone'
    HR       = 'hr',       'HR only'
    MANAGERS = 'managers', 'Managers only'
    EXEC     = 'exec',     'Executive only'
    GROUPS   = 'groups',   'Specific groups'


class Colour(models.TextChoices):
    NONE   = '',       'No label'
    YELLOW = 'yellow', 'Yellow'
    GREEN  = 'green',  'Green'
    BLUE   = 'blue',   'Blue'
    RED    = 'red',    'Red'
    GREY   = 'grey',   'Grey'


def audience_allows(audience, groups_qs, user):
    """Shared visibility test. Delegates role questions to the existing helpers."""
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True

    from cms.models import _user_is_manager, EXECUTIVE_GROUP
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


def user_can_manage_dam(user):
    """May this user upload, rename, move or delete library content?

    Deliberately broad, matching the CMS: superusers, the Django model
    permission, the Document Managers group — and Intranet Admin, which is the
    Hub's existing platform-administrator group. Without that last clause an
    Intranet Admin who is not a Django superuser saw a read-only library, which
    is exactly the "I have administrator permissions yet no edit controls"
    problem this work set out to remove.
    """
    if not user or not user.is_authenticated:
        return False
    from core.models import is_intranet_admin

    return (is_intranet_admin(user)
            or user.has_perm('dam.change_asset')
            or user.groups.filter(name=DAM_MANAGERS_GROUP).exists())


class Tag(models.Model):
    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(unique=True, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:50] or 'tag'
        super().save(*args, **kwargs)


class Space(models.Model):
    """A named library, e.g. Brand Files. Gives each module a root folder."""
    name = models.CharField(max_length=120)
    slug = models.SlugField(unique=True)
    description = models.CharField(max_length=400, blank=True)
    icon = models.CharField(max_length=40, blank=True,
                            help_text='Icon name from the CMS icon registry.')
    order = models.IntegerField(default=0)
    audience = models.CharField(max_length=20, choices=Audience.choices,
                                default=Audience.EVERYONE)
    audience_groups = models.ManyToManyField(Group, blank=True, related_name='dam_spaces')

    class Meta:
        ordering = ['order', 'name']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse('dam_space', args=[self.slug])

    def is_visible_to(self, user):
        return audience_allows(self.audience, self.audience_groups, user)


class Folder(models.Model):
    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name='folders')
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True,
                               related_name='children')
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, blank=True)
    description = models.CharField(max_length=400, blank=True)

    icon   = models.CharField(max_length=40, blank=True)
    cover  = models.ImageField(upload_to='dam/covers/', blank=True, null=True)
    colour = models.CharField(max_length=10, choices=Colour.choices, blank=True, default='')

    pinned = models.BooleanField(default=False)
    order  = models.IntegerField(default=0)

    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='owned_folders',
                              help_text='Who is accountable for this folder.')
    review_date = models.DateField(null=True, blank=True,
                                   help_text='When the contents should next be reviewed.')

    audience = models.CharField(max_length=20, choices=Audience.choices,
                                default=Audience.EVERYONE)
    audience_groups = models.ManyToManyField(Group, blank=True, related_name='dam_folders')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-pinned', 'order', 'name']
        unique_together = [('parent', 'slug', 'space')]

    def __str__(self):
        return ' / '.join(f.name for f in self.ancestors_and_self())

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:180] or 'folder'
            slug, n = base, 2
            while Folder.objects.filter(space=self.space, parent=self.parent,
                                        slug=slug).exclude(pk=self.pk).exists():
                slug = f'{base}-{n}'
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    # ── tree helpers ─────────────────────────────────────────────────────
    def ancestors_and_self(self):
        """Root-first chain, used for breadcrumbs."""
        chain, node, guard = [], self, 0
        while node is not None and guard < 50:
            chain.append(node)
            node = node.parent
            guard += 1
        return list(reversed(chain))

    def descendant_ids(self):
        """This folder plus everything beneath it, for recursive counts."""
        ids, frontier = [self.pk], [self.pk]
        while frontier:
            frontier = list(Folder.objects.filter(parent_id__in=frontier)
                            .values_list('pk', flat=True))
            ids.extend(frontier)
        return ids

    def is_visible_to(self, user):
        """A folder is visible only if it and all its ancestors allow the user."""
        for node in self.ancestors_and_self():
            if not audience_allows(node.audience, node.audience_groups, user):
                return False
        return self.space.is_visible_to(user)

    def would_create_cycle(self, new_parent):
        """True if moving under new_parent would make the tree circular."""
        if new_parent is None:
            return False
        if new_parent.pk == self.pk:
            return True
        return new_parent.pk in set(self.descendant_ids())

    @property
    def asset_count(self):
        return Asset.objects.filter(folder_id__in=self.descendant_ids(),
                                    archived=False).count()


def asset_upload_to(instance, filename):
    return f'dam/{instance.folder.space.slug}/{filename}'


class Asset(models.Model):
    IMAGE = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg')
    PDF   = ('.pdf',)
    DOC   = ('.doc', '.docx', '.rtf', '.odt', '.txt')
    SHEET = ('.xls', '.xlsx', '.csv', '.ods')
    SLIDE = ('.ppt', '.pptx', '.odp')
    VIDEO = ('.mp4', '.mov', '.avi', '.mkv', '.webm')
    ARCHIVE = ('.zip', '.rar', '.7z', '.tar', '.gz')

    folder = models.ForeignKey(Folder, on_delete=models.CASCADE, related_name='assets')
    name = models.CharField(max_length=250, help_text='Display name, not the filename.')
    file = models.FileField(upload_to=asset_upload_to)
    description = models.CharField(max_length=500, blank=True)

    tags = models.ManyToManyField(Tag, blank=True, related_name='assets')

    version = models.PositiveIntegerField(default=1)
    size = models.PositiveBigIntegerField(default=0, editable=False)
    thumbnail = models.ImageField(upload_to='dam/thumbs/', blank=True, null=True,
                                  editable=False)

    # ── For images used as page content ──────────────────────────────────
    # Alt text belongs to the picture, not to each place it is used: describing
    # the same photo again every time it appears is how alt text ends up empty.
    alt_text = models.CharField(
        max_length=300, blank=True,
        help_text='What the image shows, for screen readers and when it fails to load.')

    # Where the subject is, as a percentage across and down. A wide banner crops
    # to a narrow slot from the centre by default, which beheads people.
    focal_x = models.PositiveSmallIntegerField(default=50)
    focal_y = models.PositiveSmallIntegerField(default=50)

    # Pixel size, cached on save. The crop tool cannot work without it: a crop
    # expressed against the displayed size would land somewhere else on a photo
    # that was scaled to fit the dialog. Zero means "not an image, or unreadable".
    width = models.PositiveIntegerField(default=0, editable=False)
    height = models.PositiveIntegerField(default=0, editable=False)

    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='uploaded_assets')
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='updated_assets')
    archived = models.BooleanField(default=False, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [models.Index(fields=['folder', 'archived'])]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.name and self.file:
            self.name = os.path.splitext(self.filename)[0][:250]
        # Cache the size so listings never stat the storage backend.
        try:
            self.size = self.file.size
        except Exception:
            pass
        self._read_dimensions(kwargs.get('update_fields'))
        super().save(*args, **kwargs)

    def _read_dimensions(self, update_fields=None):
        """Cache the pixel size of an image, quietly.

        Reading it here rather than on demand keeps the crop dialog from opening
        the file on every listing. A save that names its update_fields is left
        alone: it is a targeted write and must not silently grow.
        """
        if update_fields is not None and not {'file', 'width', 'height'} & set(update_fields):
            return
        if self.kind != 'image' or self.ext == '.svg':
            self.width = self.height = 0
            return
        try:
            import io
            from PIL import Image, ImageOps
            handle = self.file
            was_closed = handle.closed
            if was_closed:
                handle.open('rb')
            else:
                handle.seek(0)
            try:
                # Copied into memory and read from there. Handing the upload's own
                # handle to Pillow let it close the file, and Django then had
                # nothing left to write -- the upload died before it was stored.
                raw = handle.read()
            finally:
                try:
                    handle.seek(0)      # leave it where the caller expects it
                except Exception:
                    pass
                if was_closed:
                    handle.close()
            with Image.open(io.BytesIO(raw)) as im:
                # EXIF orientation first: a phone photo reports its sensor size,
                # which is sideways from how everyone sees it.
                im = ImageOps.exif_transpose(im)
                self.width, self.height = im.size
        except Exception:
            # An unreadable or truncated image must not stop the record saving.
            self.width = self.height = 0

    # ── presentation helpers ─────────────────────────────────────────────
    @property
    def filename(self):
        return self.file.name.rsplit('/', 1)[-1] if self.file else ''

    @property
    def ext(self):
        return os.path.splitext(self.filename)[1].lower()

    @property
    def kind(self):
        e = self.ext
        if e in self.IMAGE:   return 'image'
        if e in self.PDF:     return 'pdf'
        if e in self.DOC:     return 'doc'
        if e in self.SHEET:   return 'sheet'
        if e in self.SLIDE:   return 'slide'
        if e in self.VIDEO:   return 'video'
        if e in self.ARCHIVE: return 'archive'
        return 'file'

    @property
    def format_label(self):
        return self.ext.lstrip('.').upper() or 'FILE'

    @property
    def pretty_size(self):
        n = float(self.size or 0)
        for unit in ('B', 'KB', 'MB', 'GB'):
            if n < 1024 or unit == 'GB':
                return f'{n:.0f} {unit}' if unit in ('B', 'KB') else f'{n:.1f} {unit}'
            n /= 1024
        return f'{n:.1f} GB'

    @property
    def can_preview_inline(self):
        """Types a browser renders natively, so Preview need not download."""
        return self.kind in ('image', 'pdf', 'video')

    @property
    def focal_css(self):
        """object-position value, so a crop keeps the subject in frame."""
        return f'{self.focal_x}% {self.focal_y}%'

    def is_visible_to(self, user):
        return self.folder.is_visible_to(user)


class AssetVersion(models.Model):
    """A superseded file, kept so a replace is never destructive."""
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name='versions')
    file = models.FileField(upload_to='dam/versions/')
    version = models.PositiveIntegerField()
    note = models.CharField(max_length=300, blank=True)
    size = models.PositiveBigIntegerField(default=0)
    replaced_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-version']

    def __str__(self):
        return f'{self.asset.name} v{self.version}'

    @property
    def filename(self):
        return self.file.name.rsplit('/', 1)[-1] if self.file else ''

    @property
    def pretty_size(self):
        n = float(self.size or 0)
        for unit in ('B', 'KB', 'MB', 'GB'):
            if n < 1024 or unit == 'GB':
                return f'{n:.0f} {unit}' if unit in ('B', 'KB') else f'{n:.1f} {unit}'
            n /= 1024
        return f'{n:.1f} GB'
