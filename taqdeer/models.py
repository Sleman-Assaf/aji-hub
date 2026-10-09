"""AJi Taqdeer — recognition.

Two things live here, and they are deliberately one app rather than two:

  Kudos              a colleague thanking a colleague. No approval, no
                     ceremony, no limit. Written and posted in one step.

  The Athar  الأثر   the quarterly award. Managers nominate, HR reviews and
                     corrects, HR approves, and approval generates a signed
                     certificate that is emailed from HR's own address.

They share a page and a profile block, so a person's recognition reads as one
history rather than two lists that happen to be about the same person.

Milestones — a certification earned, a course completed — are the third kind
the proposal described. Nothing here forbids them; they are not built yet.
"""
from django.contrib.auth.models import Group, User
from django.db import models
from django.utils import timezone


class Praise(models.Model):
    """One Kudos: who thanked whom, for what.

    Nothing is approved before it appears. The check on this is that the giver
    is always named — praise nobody signs is not praise — and that HR can hide
    an entry without deleting it, so a bad one leaves a trace rather than a gap.
    """
    giver = models.ForeignKey(User, on_delete=models.CASCADE,
                              related_name='praise_given')
    recipient = models.ForeignKey(User, on_delete=models.CASCADE,
                                  related_name='praise_received')
    subject = models.CharField(max_length=140)
    message = models.TextField()

    #: Optional context. A praise tied to a project is worth more to HR a year
    #: later than one that only says somebody was helpful.
    category = models.CharField(max_length=60, blank=True)
    #: When the giver last changed the wording. Shown beside the date, because a
    #: Kudos is a public statement about somebody and quietly rewriting one
    #: after it has been read — and liked, and replied to — should not be
    #: invisible.
    edited_at = models.DateTimeField(null=True, blank=True)
    project = models.CharField(max_length=160, blank=True)
    attachment = models.FileField(upload_to='taqdeer/praise/', blank=True, null=True)

    #: Only consulted when praise_needs_approval is on. Default APPROVED so
    #: that turning moderation on later does not retrospectively hide the
    #: praise already given.
    class Status(models.TextChoices):
        PENDING = 'pending', 'Waiting for HR'
        APPROVED = 'approved', 'Published'
        REJECTED = 'rejected', 'Not published'

    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.APPROVED)

    created_at = models.DateTimeField(auto_now_add=True)

    # Moderation. Hidden rows stay in the table and stay countable by HR; they
    # simply stop rendering. A delete would lose the fact that it happened.
    hidden_at = models.DateTimeField(null=True, blank=True)
    hidden_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                  blank=True, related_name='praise_hidden')

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['recipient', '-created_at'])]

    def __str__(self):
        return f'{self.giver} → {self.recipient}: {self.subject}'

    @property
    def is_visible(self):
        return self.hidden_at is None


class AwardCycle(models.Model):
    """One quarter's round of the Athar Award.

    A cycle is what stops the award becoming a list that only grows. It moves
    in one direction — nominating, review, issued — and only one cycle is open
    for nominations at a time.
    """
    class Stage(models.TextChoices):
        #: The chain the specification asks for. The three original values keep
        #: their meaning so existing rows and code are unaffected: NOMINATING is
        #: "Open", REVIEW is "HR review", ISSUED is "Released".
        DRAFT = 'draft', 'Draft'
        NOMINATING = 'nominating', 'Open for nominations'
        SUBMISSIONS_CLOSED = 'subs_closed', 'Submissions closed'
        REVIEW = 'review', 'With HR for review'
        APPROVED = 'approved', 'Approved, ready to release'
        ISSUED = 'issued', 'Released'
        CLOSED = 'closed', 'Closed'

    year = models.PositiveIntegerField()
    quarter = models.PositiveSmallIntegerField(
        choices=[(1, 'Q1'), (2, 'Q2'), (3, 'Q3'), (4, 'Q4')])
    stage = models.CharField(max_length=12, choices=Stage.choices,
                             default=Stage.NOMINATING)
    note = models.CharField(max_length=250, blank=True,
                            help_text='Shown to managers on the nomination form.')

    #: Real dates, so the window can close itself and a reminder knows when to
    #: go. Nullable because the cycles created before this existed have none.
    opens_on = models.DateField(null=True, blank=True)
    closes_on = models.DateField(null=True, blank=True)
    release_on = models.DateField(null=True, blank=True)

    #: Who may nominate into this cycle, and who may be nominated. Empty means
    #: "anyone the role rules already allow" rather than "nobody" — a cycle
    #: opened without configuring eligibility should still work.
    submitter_groups = models.ManyToManyField(
        Group, blank=True, related_name='athar_may_submit')
    recipient_groups = models.ManyToManyField(
        Group, blank=True, related_name='athar_may_receive')
    authorised_managers = models.ManyToManyField(
        User, blank=True, related_name='athar_authorised_for',
        help_text='Named managers, when eligibility is not by role.')

    awards_per_manager = models.PositiveIntegerField(
        default=5, help_text='How many people one manager may put forward.')

    #: Overrides the global setting for this quarter only.
    visibility_days = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    issued_at = models.DateTimeField(null=True, blank=True)

    #: When the "submissions close soon" reminder went out for this quarter.
    #: Recorded rather than recomputed so the reminder command can be run hourly
    #: — which is the only way to survive a server that was asleep at the hour —
    #: without any risk of a manager being reminded twice for one quarter.
    reminder_sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    released_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name='athar_released')

    class Meta:
        ordering = ['-year', '-quarter']
        unique_together = [('year', 'quarter')]

    def __str__(self):
        return self.label

    @property
    def label(self):
        return f'Q{self.quarter} {self.year}'

    @property
    def is_open(self):
        return self.stage == self.Stage.NOMINATING

    @classmethod
    def current(cls):
        """The cycle managers may nominate into, or None."""
        return cls.objects.filter(stage=cls.Stage.NOMINATING).first()

    @classmethod
    def for_today(cls):
        """The cycle covering today, created if it does not exist yet."""
        now = timezone.now()
        quarter = (now.month - 1) // 3 + 1
        obj, _ = cls.objects.get_or_create(year=now.year, quarter=quarter)
        return obj


class Nomination(models.Model):
    """A manager putting one person forward, and what HR did with it.

    `recipient` is the directory record, so the award can reach a profile and an
    inbox. `recipient_name` is what gets printed — prefilled from the directory
    and editable by HR, because the brief was explicit that HR must be able to
    fix a name that was written wrong without rejecting the whole nomination.
    """
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SUBMITTED = 'submitted', 'Waiting for HR'
        RETURNED = 'returned', 'Returned to the manager'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Not taken forward'

    cycle = models.ForeignKey(AwardCycle, on_delete=models.CASCADE,
                              related_name='nominations')
    nominated_by = models.ForeignKey(User, on_delete=models.CASCADE,
                                     related_name='nominations_made')
    recipient = models.ForeignKey(User, on_delete=models.CASCADE,
                                  related_name='nominations_received')
    recipient_name = models.CharField(
        max_length=160,
        help_text='As it should appear on the certificate.')

    #: The line that follows "In recognition of the outstanding performance and
    #: achievement in…". One sentence — a citation needing a paragraph is a news
    #: post, not a certificate.
    citation = models.TextField(help_text='Why they are receiving the award.')

    status = models.CharField(max_length=10, choices=Status.choices,
                              default=Status.SUBMITTED)
    hr_note = models.TextField(blank=True,
                               help_text='Shown to the manager when returned.')
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name='nominations_reviewed')
    reviewed_at = models.DateTimeField(null=True, blank=True)

    certificate = models.FileField(upload_to='taqdeer/certificates/', blank=True)
    emailed_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-cycle__year', '-cycle__quarter', 'recipient_name']

    def __str__(self):
        return f'{self.recipient_name} — {self.cycle.label}'

    @property
    def is_approved(self):
        return self.status == self.Status.APPROVED


# ── settings ────────────────────────────────────────────────────────────────

class TaqdeerSettings(models.Model):
    """One row, holding everything the specification calls configurable.

    A settings singleton rather than a table of key/value pairs: these are
    read on nearly every Taqdeer page, they have real types, and a typo in a
    key name should be a migration error rather than a silently missing value.
    """
    class Visibility(models.TextChoices):
        W1 = '7', 'One week'
        W2 = '14', 'Two weeks'
        M1 = '30', 'One month'
        M2 = '60', 'Two months'
        M3 = '90', 'Three months'
        PERMANENT = '0', 'Permanent'

    # ── Athar ──
    default_period_days = models.PositiveIntegerField(
        default=21, help_text='How long a submission window stays open by default.')
    default_awards_per_manager = models.PositiveIntegerField(default=5)
    #: Named people, not roles. HR asked for this directly: they know who the
    #: managers are and do not want to reason about which Django group somebody
    #: happens to be in. The group field below is kept so no configuration is
    #: silently lost, but nothing reads it any more.
    eligible_submitters = models.ManyToManyField(
        User, blank=True, related_name='taqdeer_may_nominate',
        help_text='Who may put people forward for the Athar Award. '
                  'Leave empty to allow every manager.')
    eligible_submitter_groups = models.ManyToManyField(
        Group, blank=True, related_name='taqdeer_submitter_for', editable=False)
    results_visibility_days = models.CharField(
        max_length=4, choices=Visibility.choices, default=Visibility.M1,
        help_text='How long released results stay on the main page.')
    results_visibility_custom = models.PositiveIntegerField(
        null=True, blank=True, help_text='Days, when a custom duration is wanted.')

    # ── certificate ──
    certificate_title = models.CharField(max_length=120, default='The Athar Award')
    certificate_recognition_line = models.CharField(
        max_length=250,
        default='In recognition of the outstanding performance and achievement in')
    #: The spec is explicit that the certificate carries the CEO alone.
    signatory_title = models.CharField(
        max_length=120, default='Chief Executive Officer',
        verbose_name='Position that signs',
        help_text='Printed in small capitals at the foot of the certificate.')
    signatory_name = models.CharField(
        max_length=120, blank=True, verbose_name='Name of the signatory',
        help_text='Printed above the position. Leave empty to print the '
                  'position on its own.')
    signature_image = models.ImageField(
        upload_to='taqdeer/signature/', blank=True, null=True,
        verbose_name='Signature',
        help_text='A scan of the signature — PNG with a transparent '
                  'background. Drawn across the signature line.')

    # ── notifications ──
    #: A draft announces nothing to staff — that is the point of a draft, and the
    #: create button says so. This one goes to the other people who run Taqdeer,
    #: so a quarter being drafted is not something a colleague finds by accident.
    notify_period_draft = models.BooleanField(
        default=True,
        help_text='Tells the other HR administrators, never the nominators.')
    notify_period_open = models.BooleanField(default=True)
    notify_manager_assigned = models.BooleanField(
        default=True, verbose_name='Tell a manager when you assign them',
        help_text='Sent the moment you add somebody to a quarter, rather than '
                  'waiting for it to open. Without it they only hear when the '
                  'quarter opens, and never at all if you add them to one that '
                  'is already open.')
    notify_reminder = models.BooleanField(default=True)
    reminder_days_before = models.PositiveIntegerField(default=3)
    notify_submission = models.BooleanField(default=True)
    notify_returned = models.BooleanField(default=True)
    notify_approved = models.BooleanField(default=True)
    notify_release = models.BooleanField(default=True)
    congratulations_message = models.TextField(
        default='We are pleased to recognise you as an Athar award recipient. '
                'Your certificate is now available in AJi Taqdeer.',
        help_text='Sent to each recipient when a quarter is released.')

    # ── praise ──
    praise_needs_approval = models.BooleanField(
        default=False, help_text='When on, a praise waits for HR before it appears.')
    praise_givers = models.ManyToManyField(
        User, blank=True, related_name='taqdeer_may_praise',
        help_text='Who may send Kudos. Leave empty to allow everybody, '
                  'which is the usual answer.')
    praise_giver_groups = models.ManyToManyField(
        Group, blank=True, related_name='taqdeer_praise_for', editable=False)
    praise_allow_attachment = models.BooleanField(default=True)

    # ── certifications ──
    certification_template = models.TextField(
        default='We are proud to share with you that {employee} has successfully '
                'obtained the {certification} certification. Congratulations!',
        help_text='{employee} and {certification} are replaced when it is shown.')
    certification_on_homepage = models.BooleanField(default=True)
    certification_on_taqdeer = models.BooleanField(default=True)
    certification_on_profile = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'AJi Taqdeer settings'
        verbose_name_plural = 'AJi Taqdeer settings'

    def __str__(self):
        return 'AJi Taqdeer settings'

    @classmethod
    def get(cls):
        """The single row, created on first use."""
        obj = cls.objects.first()
        if obj is None:
            obj = cls.objects.create()
        return obj

    @property
    def visibility_days(self):
        """How many days results stay up. 0 means forever."""
        if self.results_visibility_custom:
            return self.results_visibility_custom
        return int(self.results_visibility_days)


# ── certifications ──────────────────────────────────────────────────────────

class Certification(models.Model):
    """A credential somebody earned, announced in the house template.

    HR types a name and a date; the wording comes from settings so that a
    hundred of these read identically and nobody has to design one.
    """
    employee = models.ForeignKey(User, on_delete=models.CASCADE,
                                 related_name='certifications')
    name = models.CharField(max_length=200, help_text='e.g. PMP, LEED AP, ISO 9001 Lead Auditor')
    issuer = models.CharField(max_length=160, blank=True)
    obtained_on = models.DateField()
    logo = models.ImageField(upload_to='taqdeer/certifications/', blank=True, null=True)

    published = models.BooleanField(default=True)
    added_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                 blank=True, related_name='certifications_added')
    created_at = models.DateTimeField(auto_now_add=True)

    #: The rendered congratulations card, kept rather than redrawn. Drawing one
    #: costs about a second of CPU, which is fine once and absurd on every
    #: request of a feed. Cleared automatically when anything printed on it
    #: changes, so a corrected name still produces a corrected card.
    card = models.ImageField(upload_to='taqdeer/cards/', blank=True, null=True,
                             editable=False)

    #: What the stored card was drawn from, so the model can tell whether it is
    #: still current without re-rendering to compare.
    card_key = models.CharField(max_length=200, blank=True, editable=False)

    class Meta:
        ordering = ['-obtained_on', '-created_at']
        indexes = [models.Index(fields=['-obtained_on'])]

    def __str__(self):
        return f'{self.employee} — {self.name}'

    @property
    def _key(self):
        """Everything the card prints. Change any of it and the card is stale."""
        who = self.employee.get_full_name() or self.employee.username
        return f'{who}|{self.name}|{self.issuer}|{self.obtained_on}'

    def save(self, *args, **kwargs):
        # Throw the cached card away when what it says has changed. Done here
        # rather than in the view so it holds however the row is edited —
        # admin, shell, a future import.
        if self.pk and self.card and self.card_key != self._key:
            # Drop the reference first, then try to remove the file. On Windows
            # a response still streaming the old card holds a lock on it, and a
            # rename must not fail because somebody is looking at the picture.
            # An orphan on disk is cheaper than a 500 on a name correction.
            stale = self.card
            self.card = None
            self.card_key = ''
            try:
                stale.delete(save=False)
            except OSError:
                pass
        super().save(*args, **kwargs)

    def card_file(self):
        """The card, drawn once and kept — and redrawn if the file went missing.

        The check used to be "is a card recorded, and is its key still current?",
        which trusts the database about the contents of a folder. When the file
        is gone — media restored without it, somebody tidying up, a deploy that
        did not carry media across — both those answers are still yes, and the
        view then opened a path that is not there and returned a 500 on every
        request for that card.

        Redrawing is always safe: the card is a pure function of the name, the
        certification, the issuer and the date, all of which are in this row.
        `storage.exists` rather than `os.path` so this still holds if media ever
        moves off local disk.
        """
        if (self.card and self.card_key == self._key
                and self.card.storage.exists(self.card.name)):
            return self.card
        from django.core.files.base import ContentFile

        from .announcement import as_png
        png = as_png(self)
        safe = ''.join(c for c in self.name if c.isalnum() or c in ' -_').strip()
        self.card.save(f'card-{self.pk}-{safe[:40] or "cert"}.png',
                       ContentFile(png), save=False)
        self.card_key = self._key
        super().save(update_fields=['card', 'card_key'])
        return self.card

    def announcement(self, settings=None):
        """The house sentence, with this person's details in it."""
        s = settings or TaqdeerSettings.get()
        who = self.employee.get_full_name() or self.employee.username
        try:
            return s.certification_template.format(employee=who, certification=self.name)
        except (KeyError, IndexError):
            # A template edited into an invalid form must not take the page down.
            return f'{who} has obtained the {self.name} certification.'


# Likes and comments live in their own module but must be imported here for
# Django to see them as part of this app.
from .social import Comment, Reaction  # noqa: E402,F401
