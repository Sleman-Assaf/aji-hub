# exchange/models.py
"""AJi Exchange — communities, threads and the documents shared in them.

Three things shape this file.

**A community can be private.** Membership used to be a free-for-all: every
community was listed, and Join added you. A private community is still listed —
hiding it would mean people asking to be added to something they cannot see —
but its threads, its documents and its member list are closed. Non-members get
the name, the description, a member count and a way to ask.

**Deleting a community is recoverable.** A community is a year of somebody's
discussion; a mis-click should not end it. `deleted_at` hides it from every
ordinary query, and an Intranet Admin can bring it back with its threads intact.
The filtering happens in the default manager rather than at each call site,
because a soft delete every query has to remember to exclude is one that leaks.

**Attachments belong to threads and to the community at once.** A file posted in
a thread should also appear in Documents without anybody filing it twice, so
`Attachment` carries both keys and a thread post writes both.
"""
from datetime import timedelta
from math import ceil

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

#: Slugs that would shadow a route under /exchange/. A community called "New"
#: is reasonable; a community whose URL swallows the create-community page is
#: not, and the collision only shows up the next time somebody clicks Create.
RESERVED_SLUGS = {'new', 'api', 'requests', 'deleted', 'admin', 'search'}

#: Thread attachments. Big enough for a drawing set or a marked-up PDF, small
#: enough that one careless upload does not fill the disk.
MAX_ATTACHMENT_BYTES = 35 * 1024 * 1024


def is_intranet_admin(user):
    """Site-wide administrators, who can reach any community."""
    from core.models import is_intranet_admin as _core
    return _core(user)


class LiveCommunityManager(models.Manager):
    """Hides removed communities. `Community.all_objects` sees everything."""
    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


class Community(models.Model):
    class Visibility(models.TextChoices):
        PUBLIC = 'public', 'Public'
        PRIVATE = 'private', 'Private'

    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    description = models.TextField(blank=True)
    icon = models.CharField(max_length=50, blank=True, help_text='Icon key from hub icon set')
    members = models.ManyToManyField(User, blank=True, related_name='communities')

    visibility = models.CharField(
        max_length=10, choices=Visibility.choices, default=Visibility.PUBLIC,
        help_text='Public: anyone may read and join. Private: listed, but only '
                  'members see inside.')
    # Null on the communities that existed before this field, which is why every
    # permission check treats "no creator" as "admins only" rather than failing.
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='communities_created')
    created_at = models.DateTimeField(auto_now_add=True)

    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)
    deleted_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                   blank=True, editable=False,
                                   related_name='communities_deleted')

    objects = LiveCommunityManager()
    #: Includes removed communities. Used by the restore list and by restore.
    all_objects = models.Manager()

    class Meta:
        verbose_name_plural = 'Communities'
        ordering = ['name']

    def __str__(self):
        return self.name

    def member_count(self):
        return self.members.count()

    # ── Permissions ──────────────────────────────────────────────────────
    # Every one of these takes a user rather than reading request state, so the
    # same rule answers for a template, a view and a test.

    def is_member(self, user):
        if not user or not user.is_authenticated:
            return False
        return self.members.filter(pk=user.pk).exists()

    def is_admin(self, user):
        """May rename, add and remove members, pin, and delete.

        The creator and the Intranet Admins — not every member, because a
        community with fifty members would otherwise have fifty people able to
        delete it.
        """
        if not user or not user.is_authenticated:
            return False
        if is_intranet_admin(user):
            return True
        return self.created_by_id is not None and self.created_by_id == user.pk

    def can_read(self, user):
        """Whether this user may see threads, documents and members."""
        if self.visibility == self.Visibility.PUBLIC:
            return bool(user and user.is_authenticated)
        return self.is_member(user) or self.is_admin(user)

    def can_post(self, user):
        """Only members write, in a public community as much as a private one."""
        return self.is_member(user)

    @property
    def is_private(self):
        return self.visibility == self.Visibility.PRIVATE

    # ── Removal ──────────────────────────────────────────────────────────

    #: How long a removed community is kept before the sweep is willing to
    #: destroy it. Long enough that somebody back from a fortnight's leave can
    #: still say "put that back", which is the case the grace period is for.
    PURGE_AFTER_DAYS = 15

    def soft_delete(self, by=None):
        self.deleted_at = timezone.now()
        self.deleted_by = by if (by and by.is_authenticated) else None
        self.save(update_fields=['deleted_at', 'deleted_by'])

    def restore(self):
        self.deleted_at = None
        self.deleted_by = None
        self.save(update_fields=['deleted_at', 'deleted_by'])

    @property
    def purge_due_at(self):
        """When the sweep becomes willing to destroy this one, or None."""
        if not self.deleted_at:
            return None
        return self.deleted_at + timedelta(days=self.PURGE_AFTER_DAYS)

    @property
    def days_until_purge(self):
        """Whole days left before the sweep would take it. 0 means overdue.

        Rounded up, so a community with six hours left reads as "1 day" rather
        than "0 days" while it is still recoverable.
        """
        due = self.purge_due_at
        if due is None:
            return None
        remaining = (due - timezone.now()).total_seconds()
        if remaining <= 0:
            return 0
        return ceil(remaining / 86400)

    @classmethod
    def expired(cls, days=None, now=None):
        """Removed communities old enough for the sweep to take."""
        days = cls.PURGE_AFTER_DAYS if days is None else days
        cutoff = (now or timezone.now()) - timedelta(days=days)
        return cls.all_objects.filter(deleted_at__isnull=False,
                                      deleted_at__lte=cutoff)

    @classmethod
    def purge_expired(cls, days=None, now=None, dry_run=False):
        """Destroy every removed community past the grace period.

        Returns the names taken, so a caller can say what it did rather than a
        count that gives nobody a way to check. Nothing here is recoverable —
        this is the one operation in the app that really deletes, and it exists
        so that "removed" does not quietly mean "kept for ever".
        """
        doomed = list(cls.expired(days=days, now=now))
        names = [c.name for c in doomed]
        if not dry_run:
            for community in doomed:
                community.hard_delete()
        return names

    def hard_delete(self):
        """Destroy the community and everything under it, for good.

        Threads, replies, attachments and join requests all cascade. Deliberately
        not called `delete` — Django calls that itself in places, and a name that
        looks ordinary is the wrong name for the only irreversible thing here.
        """
        super().delete()

    @staticmethod
    def unique_slug(name):
        """A slug that is free and cannot shadow one of the /exchange/ routes."""
        from django.utils.text import slugify
        base = slugify(name)[:40] or 'community'
        if base in RESERVED_SLUGS:
            base = f'{base}-community'
        slug, n = base, 1
        # all_objects: a removed community still owns its slug, because
        # restoring it must not collide with something created since.
        while Community.all_objects.filter(slug=slug).exists():
            n += 1
            slug = f'{base}-{n}'
        return slug


class JoinRequest(models.Model):
    """Someone asking to be let into a private community.

    One row per person per community, reused rather than piled up: asking twice
    should not put two entries in front of whoever has to answer.
    """
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        DECLINED = 'declined', 'Declined'

    community = models.ForeignKey(Community, on_delete=models.CASCADE,
                                  related_name='join_requests')
    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='community_join_requests')
    message = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name='join_requests_resolved')

    class Meta:
        unique_together = [('community', 'user')]
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user.get_full_name() or self.user.username} → {self.community}'


class Thread(models.Model):
    class ThreadType(models.TextChoices):
        DISCUSSION = 'discussion', 'Discussion'
        QUESTION = 'question', 'Question'

    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='threads')
    title = models.CharField(max_length=300)
    body = models.TextField()
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='threads')
    thread_type = models.CharField(max_length=20, choices=ThreadType.choices, default=ThreadType.DISCUSSION)
    #: Starred by a community admin: shown in the panel beside the thread list.
    pinned = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # updated_at moves whenever the row is touched — pinning a thread would
    # otherwise make it look edited. Only an actual edit sets this.
    edited_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['-pinned', '-created_at']

    def __str__(self):
        return self.title

    def reply_count(self):
        return self.replies.count()

    def can_edit(self, user):
        """The author, as asked. Community admins may remove but not rewrite:
        putting words in somebody's mouth is a different power from tidying up.
        """
        return bool(user and user.is_authenticated and self.author_id == user.pk)

    def can_delete(self, user):
        return self.can_edit(user) or self.community.is_admin(user)


class ThreadReply(models.Model):
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name='replies')
    author = models.ForeignKey(User, on_delete=models.CASCADE)
    body = models.TextField()
    mentions = models.ManyToManyField(
        User, blank=True, related_name='thread_reply_mentions',
        help_text='Users @mentioned in this reply.',
    )
    is_answer = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    #: Set when the author changes it, so the thread can say so. A comment that
    #: silently changes after somebody replied to it is worse than one that
    #: cannot change at all.
    edited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"Reply by {self.author.get_full_name()} on {self.thread}"


class Attachment(models.Model):
    class AttachType(models.TextChoices):
        DOCUMENT = 'doc', 'Document'
        LINK = 'link', 'Link'
        VIDEO = 'video', 'Video'
        IMAGE = 'image', 'Image'

    #: Suffix -> the kind it is, so a posted file files itself. Anything not
    #: listed is a document, which is the safe answer for an unknown type.
    KIND_BY_SUFFIX = {
        'image': ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg', '.heic'),
        'video': ('.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'),
    }

    community = models.ForeignKey(Community, on_delete=models.CASCADE, related_name='attachments')
    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, null=True, blank=True, related_name='attachments')
    name = models.CharField(max_length=300)
    description = models.TextField(blank=True)
    attach_type = models.CharField(max_length=10, choices=AttachType.choices, default=AttachType.DOCUMENT)
    file = models.FileField(upload_to='exchange/%Y/%m/', blank=True, null=True)
    url = models.URLField(blank=True)
    #: Starred by a community admin, alongside starred threads.
    pinned = models.BooleanField(default=False)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    @classmethod
    def kind_for(cls, filename):
        low = (filename or '').lower()
        for kind, suffixes in cls.KIND_BY_SUFFIX.items():
            if low.endswith(suffixes):
                return kind
        return cls.AttachType.DOCUMENT

    @property
    def filesize(self):
        """A readable size, or '' when the file is missing from disk.

        A FileField pointing at a file that is no longer there raises on .size,
        and a document list is not the place to discover that.
        """
        try:
            b = self.file.size
        except Exception:
            return ''
        if b >= 1024 * 1024:
            return f'{b / 1048576:.1f} MB'
        return f'{max(b / 1024, 1):.0f} KB'

    @property
    def is_image(self):
        return self.attach_type == self.AttachType.IMAGE

    @property
    def is_viewable(self):
        """Whether the Hub's document viewer can show this without downloading.

        Images and PDFs can be read in place; a zip or a Revit file has nothing
        to show, so those keep the download. Asks the viewer's own classifier
        rather than keeping a second list of extensions that would drift from it.
        """
        if not self.file:
            return False
        from portal.documents import kind_of
        # Any kind the viewer knows, rather than a list repeated here — a
        # spreadsheet attached to a post is exactly as previewable as one filed
        # in a library, and this said otherwise for both.
        return bool(kind_of(self.file.name))
