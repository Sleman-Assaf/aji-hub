# content/models.py
import re

from django.db import models
from django.contrib.auth.models import User

#: Splits on a sentence ending, but only where the next word starts a new
#: sentence — so "5,000 m2." ends one and "St. George" does not. The lookbehind
#: keeps single-letter abbreviations from counting as an ending.
_SENTENCE_END = re.compile(r'(?<=[a-z0-9\)\]"’])[.!?]+(?:\s+|$)')

#: Words that begin a trailing modifier. A sentence too long for a card can
#: usually be ended just before one of these and still say what the project is:
#: "…consists of 26 buildings | divided within the residential and educational
#: area." Cutting at a character count instead leaves "…the residen-tial and".
_TAIL_STARTERS = {
    'and', 'or', 'but', 'which', 'that', 'where', 'while', 'whose', 'whereby',
    'through', 'throughout', 'within', 'with', 'without', 'including', 'include',
    'includes', 'included', 'consisting', 'consists', 'comprising', 'comprises',
    'divided', 'located', 'situated', 'offering', 'offers', 'featuring',
    'providing', 'provides', 'serving', 'covering', 'containing', 'contains',
    'designed', 'developed', 'aimed', 'intended', 'plus', 'alongside', 'besides',
    'in', 'on', 'at', 'to', 'for', 'from', 'by', 'as', 'over', 'under', 'across',
}

#: Never the last word of a summary — it would read as having been cut off.
_DANGLING = _TAIL_STARTERS | {
    'the', 'a', 'an', 'of', 'its', 'their', 'his', 'her', 'this', 'these',
    'those', 'it', 'they', 'we', 'is', 'are', 'was', 'were', 'be', 'been',
    'has', 'have', 'had', 'will', 'would', 'can', 'could', 'also', 'into',
    'between', 'about', 'after', 'before', 'than', 'then', 'so', 'such',
}


def _tidy_ending(text):
    """Drop trailing words that leave the line hanging, and any stray comma."""
    words = text.split()
    while words and words[-1].strip('(),;:."\'').lower() in _DANGLING:
        words.pop()
    return ' '.join(words).rstrip(' ,;:–—-')


class NewsCategory(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)

    class Meta:
        verbose_name_plural = 'News Categories'
        ordering = ['name']

    def __str__(self):
        return self.name


class NewsArticle(models.Model):
    title = models.CharField(max_length=300)
    slug = models.SlugField(unique=True)
    category = models.ForeignKey(NewsCategory, on_delete=models.SET_NULL, null=True, blank=True)
    author_name = models.CharField(max_length=200, blank=True)
    excerpt = models.CharField(
        max_length=400, blank=True,
        help_text='Short summary shown on the news feed. Falls back to the '
                  'opening of the body when left empty.')
    body = models.TextField()
    image = models.ImageField(upload_to='news/', blank=True, null=True)
    date = models.DateField()
    published = models.BooleanField(default=True)
    pinned = models.BooleanField(
        default=False, help_text='Keep this article at the top of the feed.')
    views = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']

    def __str__(self):
        return self.title

    # ── feed helpers ──────────────────────────────────────────────────────
    WORDS_PER_MINUTE = 200

    @property
    def summary(self):
        """The excerpt when the author wrote one, else the start of the body."""
        if self.excerpt:
            return self.excerpt
        text = ' '.join(self.body.split())
        return text[:200] + ('…' if len(text) > 200 else '')

    @property
    def read_minutes(self):
        words = len(self.body.split())
        return max(1, round(words / self.WORDS_PER_MINUTE))

    @property
    def is_new(self):
        from datetime import date as _date, timedelta
        return self.date >= _date.today() - timedelta(days=7)

    @property
    def initials(self):
        parts = [p for p in (self.author_name or '').split() if p]
        if not parts:
            return 'AJ'
        return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else '')).upper()


class NewsLike(models.Model):
    article = models.ForeignKey(NewsArticle, on_delete=models.CASCADE, related_name='likes')
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    class Meta:
        unique_together = ('article', 'user')


class NewsComment(models.Model):
    article = models.ForeignKey(NewsArticle, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(User, on_delete=models.CASCADE)
    text = models.TextField()
    mentions = models.ManyToManyField(
        User, blank=True, related_name='news_comment_mentions',
        help_text='Users @mentioned in this comment.',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    #: Set when the author changes it, so the thread can say so. A comment that
    #: silently changes after somebody replied to it is worse than one that
    #: cannot change at all.
    edited_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.author.get_full_name()} on {self.article}"


class NewsAttachment(models.Model):
    """An image or document attached to a news article. Images are embedded
    in the article body area; anything else is offered as a download."""

    IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg')

    article = models.ForeignKey(
        NewsArticle, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to='news/attachments/')
    caption = models.CharField(max_length=300, blank=True)
    uploaded_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']

    def __str__(self):
        return f"{self.filename} on {self.article}"

    @property
    def filename(self):
        return self.file.name.rsplit('/', 1)[-1] if self.file else ''

    @property
    def is_image(self):
        return self.filename.lower().endswith(self.IMAGE_SUFFIXES)


class CalendarEvent(models.Model):
    class EventType(models.TextChoices):
        HOLIDAY = 'holiday', 'Holiday'
        COMPANY = 'company', 'Company-Wide Event'
        REMOTE_SESSION = 'remote_session', 'Remote Session'
        ONLINE_MEETING = 'online_meeting', 'Online Meeting'
        MEETING = 'meeting', 'Meeting'
        TRAINING = 'training', 'Training'
        TOWN_HALL = 'town_hall', 'Town Hall'
        DEADLINE = 'deadline', 'Deadline'
        OTHER = 'other', 'Other'

    title = models.CharField(max_length=300)
    date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    time = models.TimeField(null=True, blank=True)

    # When it finishes. Without this an event had a start and nothing else, so
    # there was no way to say how long a meeting runs -- and therefore no way to
    # show a two-hour meeting as twice the block of a one-hour one.
    end_time = models.TimeField(
        null=True, blank=True,
        help_text='When it finishes. Leave empty and it is treated as 30 minutes.')
    location = models.CharField(max_length=300, blank=True)
    event_type = models.CharField(max_length=20, choices=EventType.choices, default=EventType.OTHER)
    all_day = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    is_public = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['date', 'time']

    def __str__(self):
        return f"{self.title} ({self.date})"


    #: What an event with no end time is assumed to run for. Short enough that a
    #: missing end never dominates the day, long enough to stay readable.
    DEFAULT_MINUTES = 30

    @property
    def duration_minutes(self):
        """How long it runs, in minutes.

        An all-day event has no meaningful duration on a time axis, and an event
        with no start time cannot be placed on one at all.
        """
        if self.all_day or not self.time:
            return None
        start = self.time.hour * 60 + self.time.minute
        if not self.end_time:
            return self.DEFAULT_MINUTES
        end = self.end_time.hour * 60 + self.end_time.minute
        # An end at or before the start would render as nothing or upwards, so it
        # falls back rather than being drawn wrong.
        return (end - start) if end > start else self.DEFAULT_MINUTES

    @property
    def start_minutes(self):
        """Minutes from midnight, for placing the block on the axis."""
        if self.all_day or not self.time:
            return None
        return self.time.hour * 60 + self.time.minute

    @property
    def time_range(self):
        """"09:00 – 10:30", or just the start when there is no end."""
        if self.all_day or not self.time:
            return 'All day' if self.all_day else ''
        out = self.time.strftime('%H:%M')
        # Only a sane end. Showing "09:00 – 08:00" while the duration quietly fell
        # back to the default would have the label contradict the block drawn.
        if self.end_time and self.end_time > self.time:
            out += ' – ' + self.end_time.strftime('%H:%M')
        return out

    @property
    def date_span(self):
        """"17 Aug – 19 Aug" when it runs past its own day, otherwise nothing.

        The day numbers are read off the date rather than formatted with `%-d`,
        which is a GNU extension and produces no day at all on Windows — where
        this happens to run.
        """
        if not self.end_date or self.end_date <= self.date:
            return ''
        start = f'{self.date.day} {self.date:%b}'
        end = f'{self.end_date.day} {self.end_date:%b}'
        # Inside one month the month is said once: "17 – 19 Aug", not
        # "17 Aug – 19 Aug", which reads like two separate things.
        if (self.date.year, self.date.month) == (self.end_date.year, self.end_date.month):
            return f'{self.date.day} – {end}'
        return f'{start} – {end}'

    @property
    def when_display(self):
        """The whole of when this event runs, as one line.

        `time_range` answers "when during the day", which is the right question
        for something on a time axis and the wrong one for a card: an event
        spanning three days has no useful clock, and one that ends at 12:45
        should not be shown only as starting at 11:45.
        """
        span, clock = self.date_span, self.time_range
        if span and clock:
            return f'{span} · {clock}'
        return span or clock


class HRCategory(models.Model):
    """Category badge for HR updates — managed by HR, not hardcoded."""
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)

    class Meta:
        verbose_name_plural = 'HR Categories'
        ordering = ['name']

    def __str__(self):
        return self.name


class HRAnnouncement(models.Model):
    class Status(models.TextChoices):
        DRAFT     = 'draft',     'Draft'
        PUBLISHED = 'published', 'Published'
        ARCHIVED  = 'archived',  'Archived'

    tag = models.CharField(max_length=100, blank=True,
                           help_text='Legacy label — prefer Category below.')
    category = models.ForeignKey(HRCategory, on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name='announcements')
    title = models.CharField(max_length=300)
    slug = models.SlugField(unique=True, blank=True)
    excerpt = models.CharField(max_length=400, blank=True,
                               help_text='Preview line on the HR feed.')
    body = models.TextField()
    image = models.ImageField(upload_to='hr/', blank=True, null=True,
                              help_text='Cover image shown on the feed and the article.')
    author_name = models.CharField(max_length=200, blank=True)
    date = models.DateField()

    status = models.CharField(max_length=12, choices=Status.choices,
                              default=Status.PUBLISHED)
    # Kept in sync from status in save() so existing published=True filters
    # elsewhere in the codebase keep working.
    published = models.BooleanField(default=True, editable=False)
    pinned = models.BooleanField(default=False)

    publish_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Leave empty to appear as soon as it is published.')
    archive_at = models.DateTimeField(
        null=True, blank=True, help_text='Leave empty to keep indefinitely.')

    views = models.PositiveIntegerField(default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-pinned', '-date']

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            from django.utils.text import slugify
            base = slugify(self.title) or 'update'
            slug, n = base, 2
            while HRAnnouncement.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f'{base}-{n}'
                n += 1
            self.slug = slug
        self.published = self.status == self.Status.PUBLISHED
        super().save(*args, **kwargs)

    # ── feed helpers ──────────────────────────────────────────────────────
    @property
    def is_live(self):
        """Published, and inside its scheduling window."""
        from django.utils import timezone
        if self.status != self.Status.PUBLISHED:
            return False
        now = timezone.now()
        if self.publish_at and self.publish_at > now:
            return False
        if self.archive_at and self.archive_at <= now:
            return False
        return True

    @property
    def state_label(self):
        from django.utils import timezone
        if self.status != self.Status.PUBLISHED:
            return self.get_status_display()
        now = timezone.now()
        if self.publish_at and self.publish_at > now:
            return 'Scheduled'
        if self.archive_at and self.archive_at <= now:
            return 'Expired'
        return 'Live'

    @property
    def summary(self):
        if self.excerpt:
            return self.excerpt
        text = ' '.join(self.body.split())
        return text[:200] + ('…' if len(text) > 200 else '')

    @property
    def read_minutes(self):
        return max(1, round(len(self.body.split()) / 200))

    @property
    def badge(self):
        return self.category.name if self.category else (self.tag or 'Update')


class HRAttachment(models.Model):
    announcement = models.ForeignKey(HRAnnouncement, on_delete=models.CASCADE,
                                     related_name='attachments')
    file = models.FileField(upload_to='hr/attachments/')
    caption = models.CharField(max_length=300, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg')

    class Meta:
        ordering = ['uploaded_at']

    def __str__(self):
        return self.filename

    @property
    def filename(self):
        return self.file.name.rsplit('/', 1)[-1] if self.file else ''

    @property
    def is_image(self):
        return self.filename.lower().endswith(self.IMAGE_SUFFIXES)


class WhatsAppChannel(models.Model):
    """An official WhatsApp channel shown in the Connect modal.

    QR images are uploaded here, so refreshing a code never needs a code change.
    """
    title = models.CharField(max_length=150)
    description = models.CharField(max_length=300, blank=True)
    qr_image = models.ImageField(upload_to='whatsapp/', blank=True, null=True,
                                 help_text='QR code image employees scan.')
    join_url = models.URLField(blank=True, help_text='Optional "Join" link.')
    order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'title']
        verbose_name = 'WhatsApp channel'

    def __str__(self):
        return self.title


class Project(models.Model):
    class Status(models.TextChoices):
        ON_SITE = 'On site', 'On Site'
        DESIGN = 'Design', 'Design'
        TENDER = 'Tender', 'Tender'
        COMPLETED = 'Completed', 'Completed'

    title = models.CharField(max_length=300)
    location = models.CharField(max_length=300)
    division = models.CharField(max_length=100)
    type = models.CharField(max_length=100)
    # Optional. Most of the portfolio has no year recorded, and a required field
    # forced something to be put there — which is how 321 projects came to be
    # dated with the year they happened to be imported. Empty is now sayable.
    year = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=30, choices=Status.choices)
    image = models.ImageField(upload_to='projects/', blank=True, null=True)
    image_seed = models.CharField(max_length=100, blank=True, help_text='Seed string for placeholder image')
    #: Off by default, and the default is the point. Every project without a
    #: photograph used to pull a stand-in from picsum.photos — one external
    #: request per card, just under a second each, twenty-eight of them on the
    #: first screen of the portfolio. That was the site "running so laggy".
    #: A project that wants a stand-in can still have one; it is drawn in the
    #: browser now and costs nothing.
    use_placeholder = models.BooleanField(
        default=False, verbose_name='Place dummy picture',
        help_text='Show a stand-in graphic while there is no real photograph.')
    description = models.TextField(blank=True)

    #: Retired. The homepage strip was curated from here — editors put projects
    #: on a carousel and arranged them — and it now shows the three newest,
    #: always. Nothing reads these two columns: the view, the URL and the
    #: buttons are gone, and cms.providers.project_reel orders by year.
    #:
    #: Kept rather than dropped so the existing choices survive if the strip is
    #: ever curated again; a migration that deletes them throws that away for a
    #: few bytes a row. Do not add a reader without also restoring the cap,
    #: which lived in the view and not on the field.
    in_carousel = models.BooleanField(
        default=False, verbose_name='On the homepage carousel (retired)',
        help_text='No longer used. The homepage shows the three newest projects.')
    carousel_order = models.PositiveIntegerField(
        default=0, verbose_name='Carousel position (retired)',
        help_text='No longer used.')

    class Meta:
        ordering = ['-year', 'title']

    def __str__(self):
        return self.title

    #: Retired with the two fields above. Left as the record of what the cap was,
    #: because it was enforced in the view rather than by the field and would
    #: otherwise have to be rediscovered.
    CAROUSEL_MAX = 10

    #: About two lines on a card — enough to say what the project is, not enough
    #: to compete with its name.
    BLURB_CHARS = 120

    @property
    def blurb(self):
        """A short summary of the description, for a card.

        Sentences, not characters. These descriptions nearly always open by
        saying what the project is — "The Marina Village is part of the Ayla
        Development in Aqaba, organized into 4 neighborhoods." — so the first
        one or two of them summarise it. Cutting at a fixed character count
        instead produced fragments that stopped mid-thought: "...then will be
        distributed based on…", which is a truncation, not a description.

        Whitespace is collapsed first, because these come from a spreadsheet and
        many are bulleted lists whose newlines would otherwise arrive as long
        runs of space.
        """
        text = ' '.join((self.description or '').split())
        if not text:
            return ''

        # Sliced at the boundaries rather than split on them, so each sentence
        # keeps its full stop. `split` drops the delimiter, which left every
        # complete summary ending without punctuation — reading as though it had
        # been cut off at exactly the point it had not.
        sentences, last = [], 0
        for match in _SENTENCE_END.finditer(text):
            sentences.append(text[last:match.end()])
            last = match.end()
        if last < len(text):
            sentences.append(text[last:])

        out = ''
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            candidate = f'{out} {sentence}'.strip()
            # Whole sentences while they fit. A short opener like "Repairing
            # works for existing phosphoric acid tanks." leaves room for the
            # next one; a long one is taken alone.
            if len(candidate) <= self.BLURB_CHARS:
                out = candidate
            else:
                break
        if out:
            return out

        # The first sentence alone is too long for two lines, so it has to end
        # early — but it should end somewhere a sentence could end. Every clause
        # break and every trailing modifier inside the limit is a candidate; the
        # latest one that still leaves a usable amount of text wins, so the
        # summary is as full as it can be while remaining a statement.
        head = text[:self.BLURB_CHARS]
        # Half a card's worth. Set higher, a summary that could have ended
        # cleanly at "…consists of 26 buildings" was rejected for being a few
        # characters short and the next stop landed inside the modifier after
        # it. Shorter and whole beats longer and broken.
        floor = int(self.BLURB_CHARS * 0.5)
        cuts = set()

        for mark in (';', ',', '—', '–', ':'):
            index = head.find(mark)
            while index > 0:
                cuts.add(index)
                index = head.find(mark, index + 1)

        position = 0
        for word in head.split(' '):
            if word.strip('(),;:.').lower() in _TAIL_STARTERS and position > 0:
                cuts.add(position)
            position += len(word) + 1

        options = [c for c in (_tidy_ending(head[:cut]) for cut in sorted(cuts)) if c]
        # The *earliest* stop that is still long enough, not the latest. Taking
        # the latest maximises length and lands in the middle of whatever
        # modifier was running at the limit — "…organized into 4 neighborhoods,
        # in addition." The first substantial stopping point is a whole thought.
        full = [c for c in options if len(c) >= floor]
        if full:
            return min(full, key=len) + '.'
        if options:
            return max(options, key=len) + '.'
        return (_tidy_ending(head.rsplit(' ', 1)[0]) or head) + '…'
