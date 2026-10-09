# core/models.py
from django.db import models
from django.contrib.auth.models import User


class Role(models.TextChoices):
    EMPLOYEE = 'employee', 'Employee'
    HR = 'hr', 'HR / People & Culture'
    CONTENT_EDITOR = 'content_editor', 'Content Editor'
    IT_ADMIN = 'it_admin', 'IT Admin'
    SUPER_ADMIN = 'super_admin', 'Super Admin'


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=30, choices=Role.choices, default=Role.EMPLOYEE)

    def __str__(self):
        return f"{self.user.get_full_name()} ({self.get_role_display()})"

    def is_hr(self):
        return self.role in [Role.HR, Role.SUPER_ADMIN]

    def is_content_editor(self):
        return self.role in [Role.CONTENT_EDITOR, Role.SUPER_ADMIN]

    def is_it_admin(self):
        return self.role in [Role.IT_ADMIN, Role.SUPER_ADMIN]

    def is_super_admin(self):
        return self.role == Role.SUPER_ADMIN


class Notification(models.Model):
    class Type(models.TextChoices):
        INFO = 'info', 'Info'
        SUCCESS = 'success', 'Success'
        WARNING = 'warning', 'Warning'
        BIRTHDAY = 'birthday', 'Birthday'
        NEWS = 'news', 'News'
        MENTION = 'mention', 'Mention'

    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    type = models.CharField(max_length=20, choices=Type.choices, default=Type.INFO)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    link = models.CharField(max_length=300, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.recipient.get_full_name()} — {self.title}"


class NotificationEvent(models.TextChoices):
    """What happened. Three kinds are worth telling somebody about in more than
    one way; everything else stays in the bell, where it always was."""
    CHAT = 'chat', 'Chat messages'
    MENTION = 'mention', '@mentions'
    APPROVAL = 'approval', 'Approvals and requests'
    RENEWAL = 'renewal', 'Renewals and expiry reminders'
    GENERAL = 'general', 'Everything else'


class NotificationChannel(models.TextChoices):
    IN_APP = 'in_app', 'In the Hub'
    BROWSER = 'browser', 'Desktop pop-up'
    EMAIL = 'email', 'Email'


class NotificationPreference(models.Model):
    """One person's answer to "tell me about what, and how?".

    A field per event per channel rather than a JSON blob: these are queried in
    bulk by the digest command, and a column can be indexed and filtered where a
    blob cannot.

    The defaults are chosen so nobody is spammed on day one. Everything goes in
    the bell. Desktop pop-ups are on, because that is the point of asking. Email
    is on only for the two kinds nobody wants to miss — being named, and
    something waiting on them — and off for chat, which the 5pm digest covers in
    one message instead of forty.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='notif_prefs')

    chat_in_app = models.BooleanField(default=True)
    chat_browser = models.BooleanField(default=True)
    chat_email = models.BooleanField(default=False)

    mention_in_app = models.BooleanField(default=True)
    mention_browser = models.BooleanField(default=True)
    mention_email = models.BooleanField(default=True)

    approval_in_app = models.BooleanField(default=True)
    approval_browser = models.BooleanField(default=True)
    approval_email = models.BooleanField(default=True)

    #: Renewals and expiries (documents, certificates, licences). Email on by
    #: default — an expiry nobody heard about is the expensive kind — and the
    #: desktop pop-up off, because a deadline weeks away is not an interruption.
    #: These three exist in migration 0014 and therefore in the database; the
    #: model MUST carry them, or every fresh row's INSERT omits NOT NULL columns
    #: and MSSQL refuses it. (That happened once: the fields were dropped from
    #: the model on one checkout after another had already migrated the shared
    #: database.)
    renewal_in_app = models.BooleanField(default=True)
    renewal_browser = models.BooleanField(default=False)
    renewal_email = models.BooleanField(default=True)

    #: The 5pm summary of unread chat messages. Email only — a desktop pop-up
    #: needs a tab open, which is exactly what somebody who has missed forty
    #: messages does not have.
    digest_email = models.BooleanField(
        default=True,
        help_text='One email at the end of the day listing unread messages.')
    digest_hour = models.PositiveSmallIntegerField(
        default=17, help_text='Hour of the day, 0–23, in Asia/Amman.')
    digest_last_sent = models.DateField(null=True, blank=True, editable=False)

    def __str__(self):
        return f"Notification settings — {self.user.get_full_name() or self.user.username}"

    @classmethod
    def for_user(cls, user):
        obj, _ = cls.objects.get_or_create(user=user)
        return obj

    #: Which events have their own switches. Anything else is GENERAL, which is
    #: always in the bell and never emailed, matching how the Hub behaved before
    #: any of this existed.
    SWITCHED = (NotificationEvent.CHAT, NotificationEvent.MENTION,
                NotificationEvent.APPROVAL, NotificationEvent.RENEWAL)

    def wants(self, event, channel):
        """Should this person be told about `event` over `channel`?"""
        if event not in self.SWITCHED:
            return channel == NotificationChannel.IN_APP
        return bool(getattr(self, f'{event}_{channel}', False))

    def matrix(self):
        """[(event, label, {channel: bool})] — what the settings page draws."""
        out = []
        for event in self.SWITCHED:
            out.append((event, NotificationEvent(event).label, {
                c: getattr(self, f'{event}_{c}') for c in NotificationChannel.values
            }))
        return out


class UserPreference(models.Model):
    """Small choices the Hub should still remember next time somebody signs in.

    Separate from NotificationPreference, which answers "tell me about what?".
    This is for the state of the interface itself, and a column per choice for
    the same reason: these get read on ordinary page loads, where a JSON blob
    would have to be parsed before it could be believed.

    Deliberately not the session. A session ends at logout, and "the view I left
    it on" is expected to survive that — it is a preference, not a place in a
    journey.
    """
    class CalendarView(models.TextChoices):
        WEEK  = 'week',  'Week'
        MONTH = 'month', 'Month'
        YEAR  = 'year',  'Year'

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='prefs')

    #: Blank until somebody picks one, so the default lives in one place rather
    #: than being frozen into every existing row the day this was added.
    calendar_view = models.CharField(
        max_length=10, blank=True, default='', choices=CalendarView.choices,
        help_text='Which calendar layout this person last chose.')

    def __str__(self):
        return f"Preferences — {self.user.get_full_name() or self.user.username}"

    @classmethod
    def for_user(cls, user):
        obj, _ = cls.objects.get_or_create(user=user)
        return obj


INTRANET_ADMIN_GROUP = 'Intranet Admin'
HR_GROUP = 'HR'
IT_GROUP = 'IT'


def is_intranet_admin(user):
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name=INTRANET_ADMIN_GROUP).exists()
    )


def user_can_manage_settings(user):
    """May this user open Global Settings?

    IT as well as Intranet Admin: the settings on that page are mail transports,
    LDAP and API credentials, which is IT's work rather than the intranet
    administrator's. A superuser passes through is_intranet_admin.

    One function rather than the rule written at each site, for the same reason
    user_can_manage_policies and user_can_manage_branding exist: the check on
    the view and the flag that decides whether the menu entry appears have to be
    the same rule, or the menu offers a page the view then refuses.
    """
    if not user or not user.is_authenticated:
        return False
    return is_intranet_admin(user) or user.groups.filter(name=IT_GROUP).exists()


def user_is_hr(user):
    """Single source of truth for "may this user manage HR content?".

    The codebase previously grew three separate checks — the profile role,
    an 'HR' auth group, and a superuser flag — so this accepts all of them
    to stay compatible with however existing accounts were set up.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    try:
        if user.profile.is_hr():
            return True
    except Exception:
        pass
    return user.groups.filter(name=HR_GROUP).exists()


POLICIES_GROUP = 'Policies'


def user_can_manage_policies(user):
    """May this user create and edit policies and SOPs?

    The Policies management screens were gated on membership of a group named
    'Policies' that had never been created, so every one of them 404'd for
    everyone except a Django superuser — which is why no policy could be added.
    Accepting Intranet Admin and HR as well matches how the rest of the Hub
    decides who administers content.
    """
    if not user or not user.is_authenticated:
        return False
    return (is_intranet_admin(user)
            or user_is_hr(user)
            or user.groups.filter(name=POLICIES_GROUP).exists())


BRANDING_GROUP = 'Branding'


def user_can_manage_branding(user):
    """May this user manage Brand Files and the AJi Booth?

    Intranet Admin counts, for the same reason it counts for policies: it is how
    the rest of the Hub decides who administers content, and an intranet
    administrator locked out of the brand assets is a surprise rather than a
    policy.

    One function rather than the rule written out at each site. It had been
    spelled out in four places — the decorator on every management view, and the
    is_branding_member flag computed separately on three pages — which is how
    the Brand Files heading came to offer a Manage button to a group the view
    behind it then refused. The same trap is why user_can_manage_policies
    exists, and this is its counterpart.
    """
    if not user or not user.is_authenticated:
        return False
    return is_intranet_admin(user) or user.groups.filter(name=BRANDING_GROUP).exists()


class GlobalSetting(models.Model):
    """A key/value store for system-wide settings, managed by the
    Intranet Admin group from the Global Settings page. More setting
    sections will be added over time."""
    key = models.CharField(max_length=100, unique=True)
    value = models.TextField(blank=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.key

    @classmethod
    def get(cls, key, default=''):
        row = cls.objects.filter(key=key).first()
        return row.value if row else default

    @classmethod
    def set(cls, key, value, user=None):
        row, _ = cls.objects.update_or_create(
            key=key, defaults={'value': value, 'updated_by': user},
        )
        return row


# ── AJiAssist quick prompts (configurable in Global Settings) ──────────────
KEY_ASSIST_PROMPTS = 'assist_quick_prompts'

DEFAULT_ASSIST_PROMPTS = [
    {'label': 'Break time', 'prompt': 'What is the break time?'},
    {'label': 'Smoking policy', 'prompt': 'What is the smoking policy?'},
    {'label': 'Working hours', 'prompt': 'What are the working hours?'},
    {'label': 'Dress policy', 'prompt': 'What is the dress policy?'},
    {'label': 'Branding colours', 'prompt': 'What are the branding colours?'},
]


def get_assist_prompts():
    """The quick-prompt chips shown in the AJiAssist widget. Managed by the
    Intranet Admin group on the Global Settings page; falls back to defaults."""
    import json
    raw = GlobalSetting.get(KEY_ASSIST_PROMPTS)
    if not raw:
        return list(DEFAULT_ASSIST_PROMPTS)
    try:
        data = json.loads(raw)
        return [p for p in data
                if isinstance(p, dict) and p.get('label') and p.get('prompt')]
    except (ValueError, TypeError):
        return list(DEFAULT_ASSIST_PROMPTS)


def set_assist_prompts(prompts, user=None):
    import json
    GlobalSetting.set(KEY_ASSIST_PROMPTS, json.dumps(prompts, ensure_ascii=False), user)


# ══ IT portal ═══════════════════════════════════════════════════════════════

class ITAuditLog(models.Model):
    """A record of something an administrator did.

    The Hub had no audit trail at all: roles were granted, settings changed and
    accounts disabled with nothing written down, so "who turned this off?" had
    no answer. Every action the IT portal offers writes one of these.

    Deliberately append-only in the interface — there is no edit or delete, and
    the portal never offers one. A log somebody can tidy is not a log.
    """
    class Action(models.TextChoices):
        ROLE_GRANT = 'role_grant', 'Role granted'
        ROLE_REVOKE = 'role_revoke', 'Role revoked'
        ROLE_CREATE = 'role_create', 'Role created'
        ROLE_EDIT = 'role_edit', 'Role changed'
        ROLE_DELETE = 'role_delete', 'Role deleted'
        USER_EDIT = 'user_edit', 'Account edited'
        USER_ACTIVATE = 'user_activate', 'Account enabled'
        USER_DEACTIVATE = 'user_deactivate', 'Account disabled'
        LDAP_SYNC = 'ldap_sync', 'Directory sync'
        BACKUP = 'backup', 'Backup taken'
        BACKUP_DELETE = 'backup_delete', 'Backup deleted'
        SETTING = 'setting', 'Setting changed'
        LOGIN = 'login', 'Signed in'
        LOGOUT = 'logout', 'Signed out'
        LOGIN_FAILED = 'login_failed', 'Sign-in refused'
        WORKFLOW = 'workflow', 'Request moved'
        #: Something with content in it was deleted — an Athar period and
        #: the nominations that cascaded with it, for instance. The
        #: existing choices all describe accounts, roles or settings, and
        #: filing a destructive content change under 'Request moved'
        #: would make the log answer the question wrongly rather than not
        #: at all.
        CONTENT_DELETE = 'content_delete', 'Content deleted'

    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                              related_name='it_actions')
    action = models.CharField(max_length=24, choices=Action.choices)
    #: Free text describing the change in the words the portal will show.
    summary = models.CharField(max_length=300)
    #: Who or what it was done to, when there is one.
    target = models.CharField(max_length=200, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['-created_at']),
                   models.Index(fields=['action', '-created_at'])]

    def __str__(self):
        return f'{self.get_action_display()}: {self.summary}'

    @classmethod
    def write(cls, request, action, summary, target=''):
        """Record an action. Never raises — a failed log must not fail the work
        it was describing, but it is worth knowing when it happens."""
        try:
            ip = None
            if request is not None:
                fwd = request.META.get('HTTP_X_FORWARDED_FOR', '')
                ip = (fwd.split(',')[0].strip() if fwd
                      else request.META.get('REMOTE_ADDR')) or None
            actor = getattr(request, 'user', None)
            return cls.objects.create(
                actor=actor if (actor and actor.is_authenticated) else None,
                action=action, summary=summary[:300], target=target[:200], ip=ip)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('[it] audit write failed')
            return None


class BackupRecord(models.Model):
    """A backup that was taken, and where it went.

    The file lives on disk under BACKUP_DIR; this row is the index. Kept so the
    portal can list them without trusting a directory listing to tell it who
    asked for one and when.
    """
    filename = models.CharField(max_length=200)
    size_bytes = models.BigIntegerField(default=0)
    kind = models.CharField(max_length=20, default='database')
    note = models.CharField(max_length=200, blank=True)
    taken_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                 related_name='backups_taken')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.filename

    @property
    def size_display(self):
        b = self.size_bytes or 0
        if b >= 1073741824:
            return f'{b / 1073741824:.1f} GB'
        if b >= 1048576:
            return f'{b / 1048576:.1f} MB'
        return f'{max(b / 1024, 1):.0f} KB'

    def exists(self):
        import os
        from django.conf import settings
        return os.path.exists(os.path.join(backup_dir(), self.filename))


def backup_dir():
    """Where backups are written. Configurable, because the useful place for
    them is usually a mounted share rather than beside the code."""
    import os
    from django.conf import settings
    configured = GlobalSetting.get('backup_dir', '')
    path = configured or os.path.join(settings.BASE_DIR, 'backups')
    os.makedirs(path, exist_ok=True)
    return path


#: What each role actually unlocks, in the words the IT portal shows.
#: Kept beside the helpers that enforce it so the two are read together — the
#: portal describing a role one way while the code gates it another is exactly
#: the drift this is meant to prevent.
ROLE_CAPABILITIES = {
    INTRANET_ADMIN_GROUP: [
        'Global Settings and the IT portal',
        'Every AJi Exchange community, including private ones',
        'The Documents library',
        'Delete any community or group chat',
    ],
    IT_GROUP: [
        'Global Settings: mail, calendar, LDAP and API credentials',
        'The IT portal: roles, accounts, backups, logs and security',
    ],
    'Content Editors': [
        'Create, edit and publish Hub pages and their blocks',
        'The top navigation menu',
        'The icon library',
    ],
    HR_GROUP: [
        'HR announcements and the employee handbook',
        'Wellness, childcare and recommended certifications',
        'The HR step of training requests',
        'Anything marked "HR only"',
    ],
    'HR Managers': ['The HR approval step on a Job Requisition'],
    'CEO': ['The final approval step on a Job Requisition'],
    'Hiring Managers': ['Raise a Job Requisition for a new position'],
    'Branding': [
        'Brand Files: templates, assets and colours',
        'The AJi Booth albums',
        'Who among employees can see Brand Files',
    ],
    'Policies': ['Create and edit policies and SOPs'],
    'Document Managers': ['Upload, rename, move and delete in the Documents library'],
    'Cafeteria Staff': ['See and fulfil cafeteria orders', 'The menu and its prices'],
    'Facility Managers': ['Cafeteria reports and settings'],
    'Reception Desk': ['Approve, reject or change meeting-room bookings'],
}


def role_capabilities(name):
    return ROLE_CAPABILITIES.get(name, [])


# ── the wording of every email the Hub sends ──────────────────────────────

class EmailTemplate(models.Model):
    """What an administrator has changed about one email case.

    The cases themselves are declared in `core.email_cases`, because a case only
    exists if something in the code calls it. This holds the overrides: the
    switch, the wording, and the two automations. A row is created the first time
    somebody edits a case, so an untouched Hub has none of these and every email
    goes out with the default wording in the registry.

    `subject` and `body` blank both mean "use the default". That is why they are
    blank rather than pre-filled: filling them in would freeze today's wording
    into the database, and a later improvement to a default would then reach
    nobody who had ever opened the editor.
    """

    #: The registry code, e.g. 'athar.period_open'. Not a choices field: the
    #: registry is the authority on what codes exist and it changes with the
    #: code, not with a migration.
    code = models.CharField(max_length=80, unique=True)

    enabled = models.BooleanField(
        default=True,
        help_text='Off means this email is never sent. The in-app notification '
                  'still appears.')
    subject = models.CharField(
        max_length=300, blank=True,
        help_text='Leave empty to use the standard subject.')
    body = models.TextField(
        blank=True,
        help_text='Leave empty to use the standard wording. One paragraph per '
                  'line.')
    high_importance = models.BooleanField(
        default=False,
        help_text='Flags the email as high importance in Outlook.')
    attachment = models.CharField(
        max_length=40, blank=True,
        help_text='An automation that attaches a file to this email.')

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL,
                                   null=True, blank=True)

    class Meta:
        ordering = ['code']
        verbose_name = 'Email template'

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        # The house rule, enforced at the boundary rather than trusted to the
        # form: whatever route writes this row, no dash reaches an inbox from it.
        from .email_cases import no_dashes
        self.subject = no_dashes(self.subject)
        self.body = '\n'.join(no_dashes(line) for line in (self.body or '').splitlines())
        return super().save(*args, **kwargs)

    @property
    def is_customised(self):
        return bool(self.subject or self.body)
