# core/email_cases.py
"""Every email the Hub can send, declared in one place.

Before this, the wording of thirty-four different emails lived at thirty-four
call sites, so changing how the Hub speaks to staff meant a developer and a
deploy. The subject and body now come from here, and an administrator can
override any of them from the IT portal without touching code.

Two halves, deliberately:

* **This file** says what cases *exist* — their code, which module they belong
  to, when they fire, who gets them, and what the default wording is. It is
  code because a case only exists if something calls it: a case somebody
  invented in the database would be a row that never sends.
* **EmailTemplate** rows say what an administrator has *changed* — the switch,
  the subject, the body, the importance flag, the attachment. A blank subject
  or body means "use the default below", so the defaults keep working and a
  reset is a matter of clearing a field rather than remembering what was there.

Adding a case is two steps: add a CASE here, and pass its `code` as `case=` at
the call site. `test_email_cases` fails if either half is missing, so the two
cannot drift.

Placeholders are `{braces}` filled in at send time from whatever the call site
passes. An unknown placeholder is left visible rather than raising: an
administrator mistyping `{recipient}` should see it in the preview, not take
down the thing that triggered the email.
"""
import logging
import re

logger = logging.getLogger(__name__)

# ── modules ──────────────────────────────────────────────────────────────
#: The filter on the IT portal's list. A module is what a reader would call the
#: part of the Hub the email came from, not the Django app that holds the code.
MODULES = [
    ('athar', 'Athar Award'),
    ('kudos', 'Kudos'),
    ('recognition', 'Recognition feed'),
    ('training', 'Training'),
    ('news', 'AJi News'),
    ('exchange', 'AJi Exchange'),
    ('chat', 'Messaging'),
    ('rooms', 'Room booking'),
    ('admin', 'Administration'),
]
MODULE_LABELS = dict(MODULES)

# ── automations ──────────────────────────────────────────────────────────
#: What can be attached to an email. A case only offers the ones it can
#: actually produce — offering "the certificate" on a chat notification would be
#: a switch that silently does nothing.
ATTACH_NONE = ''
ATTACH_CERTIFICATE = 'athar_certificate'

ATTACHMENTS = {
    ATTACH_CERTIFICATE: 'The winner’s Athar certificate, as a PDF',
}


class Case:
    """One email the Hub can send.

    `subject` and `body` are the defaults. `body` may be several lines; each
    line becomes a paragraph in the email, which is what render_email already
    does with the lines it is handed.
    """

    def __init__(self, code, module, label, when, to, subject, body,
                 placeholders=(), link_label='Open in AJi Hub',
                 attachments=(), attach_by_default='', high_importance=False,
                 footer='', enabled_by_default=True, note=''):
        self.code = code
        self.module = module
        self.label = label
        self.when = when
        self.to = to
        self.subject = subject
        self.body = body
        self.placeholders = tuple(placeholders)
        self.link_label = link_label
        #: Which attachment automations this case can offer. Empty means the
        #: attachment field is not shown for it at all.
        self.attachments = tuple(attachments)
        #: On before anybody has been near the editor. The Athar certificate is
        #: the reason this exists: an award email that makes the reader go and
        #: fetch their own certificate is not how an award is given.
        self.attach_by_default = attach_by_default
        self.high_importance = high_importance
        #: Off by default for the cases that have never sent an email in their
        #: life. Declaring them here makes them editable and switchable; it must
        #: not silently start mailing people who have never had one.
        self.enabled_by_default = enabled_by_default
        self.footer = footer
        self.note = note

    @property
    def module_label(self):
        return MODULE_LABELS.get(self.module, self.module)

    def __repr__(self):
        return f'<Case {self.code}>'


#: Common placeholder sets, so the same thing is spelled the same way in every
#: case that offers it.
P_PERSON = ('name',)                      # who the email is addressed to
P_ACTOR = ('actor',)                      # who did the thing


CASES = [
    # ── Athar Award ──────────────────────────────────────────────────────
    Case('athar.period_drafted', 'athar',
         'Athar period drafted',
         'HR creates a new Athar period as a draft',
         'Everyone in the Intranet Admins group',
         '{period} Athar period drafted',
         '{actor} created it as a draft, so nothing has been sent to any manager '
         'yet. Open it to check the dates and the allowance before it opens.',
         placeholders=('period', 'actor', 'name')),

    Case('athar.manager_assigned_open', 'athar',
         'Nominator assigned, period already open',
         'HR adds you as a nominator while nominations are open',
         'Each manager newly added to the period',
         '{period}: you can put people forward for the Athar Award',
         '{name}, HR has asked you to nominate up to {allowance}. {closing}',
         placeholders=('name', 'period', 'allowance', 'closing', 'actor'),
         link_label='Open my submissions',
         high_importance=True),

    Case('athar.manager_assigned_upcoming', 'athar',
         'Nominator assigned, period not open yet',
         'HR adds you as a nominator before nominations open',
         'Each manager newly added to the period',
         '{period}: HR has asked you to nominate for the Athar Award',
         '{name}, you will be able to put forward up to {allowance}. {opening}, '
         'and you will be told again then.',
         placeholders=('name', 'period', 'allowance', 'opening', 'actor'),
         link_label='Open my submissions',
         high_importance=True),

    Case('athar.period_open', 'athar',
         'Nominations are open',
         'A period opens for nominations',
         'Every manager assigned to the period',
         '{period} Athar nominations are open',
         '{name}, you may put forward up to {allowance}. {closing}',
         placeholders=('name', 'period', 'allowance', 'closing'),
         link_label='Open my submissions',
         high_importance=True),

    Case('athar.closing_reminder', 'athar',
         'Nominations close soon',
         'A few days before nominations close, to anyone with places left',
         'Managers who have not used their full allowance',
         '{period} Athar nominations close in {days_left}',
         '{name}, {closing} You have {places_left} of your {allowance} left.',
         placeholders=('name', 'period', 'days_left', 'closing', 'places_left',
                       'allowance'),
         link_label='Open my submissions',
         high_importance=True),

    Case('athar.nomination_received', 'athar',
         'Nomination submitted, to HR',
         'A manager submits a nomination',
         'Everyone who can review nominations',
         'Athar nomination from {actor}',
         '{nominee} for {period}.',
         placeholders=('actor', 'nominee', 'period', 'name'),
         link_label='Review nominations'),

    Case('athar.nomination_receipt', 'athar',
         'Nomination submitted, receipt to the manager',
         'A manager submits a nomination, so they know it landed',
         'The manager who submitted it',
         'Your {period} nomination is with HR',
         'You put {nominee} forward. HR reviews it before anything is announced.',
         placeholders=('nominee', 'period', 'name'),
         link_label='Open my submissions'),

    Case('athar.nomination_approved_winner', 'athar',
         'Nomination approved, to the winner',
         'HR approves a nomination',
         'The person who won',
         'You have received The Athar Award',
         '{name}, {period}. {citation}',
         placeholders=('name', 'period', 'citation'),
         link_label='See it on AJi Taqdeer'),

    Case('athar.nomination_approved_manager', 'athar',
         'Nomination approved, to the manager',
         'HR approves a nomination',
         'The manager who nominated them',
         'Your nomination for {nominee} was approved',
         '{period}',
         placeholders=('nominee', 'period', 'name'),
         link_label='See it on AJi Taqdeer'),

    Case('athar.nomination_returned', 'athar',
         'Nomination sent back',
         'HR returns a nomination for a change',
         'The manager who nominated them',
         'Your nomination for {nominee} needs a change',
         '{hr_note}',
         placeholders=('nominee', 'hr_note', 'period', 'name'),
         link_label='Open my submissions'),

    Case('athar.released', 'athar',
         'Award released, the certificate email',
         'HR releases a period, to each winner who has an address on record',
         'Each winner',
         '{award_title}: {period}',
         '{name}, {congratulations} {recognition_line} {citation}',
         placeholders=('name', 'award_title', 'period', 'congratulations',
                       'recognition_line', 'citation'),
         link_label='Open your certificate',
         attachments=(ATTACH_CERTIFICATE,),
         attach_by_default=ATTACH_CERTIFICATE,
         footer='AJi Group. Design smart. Build better.',
         note='The certificate itself is a PDF the Hub already generates for '
              'this award. Turn the attachment on and it is attached to this '
              'email; leave it off and the reader has to follow the link.'),

    Case('athar.released_notice', 'athar',
         'Award released, the short notice',
         'HR releases a period, alongside the certificate email',
         'Each winner',
         'You have received {award_title}',
         '{congratulations}',
         placeholders=('name', 'award_title', 'congratulations', 'period'),
         link_label='See it on AJi Taqdeer'),

    # ── Kudos ────────────────────────────────────────────────────────────
    Case('kudos.awaiting_moderation', 'kudos',
         'Kudos waiting for HR',
         'Somebody sends Kudos while moderation is on',
         'Everyone in the HR group',
         'Kudos from {actor} is waiting',
         'For {recipient}: {subject_line}',
         placeholders=('actor', 'recipient', 'subject_line', 'name'),
         link_label='Open the Kudos queue'),

    Case('kudos.received', 'kudos',
         'Kudos received',
         'Somebody sends Kudos and moderation is off',
         'The person being recognised',
         '{actor} sent you Kudos',
         '{subject_line}',
         placeholders=('actor', 'subject_line', 'name'),
         link_label='See it on AJi Taqdeer'),

    Case('kudos.published', 'kudos',
         'Kudos published by HR',
         'HR publishes Kudos that was waiting',
         'The person being recognised',
         '{actor} sent you Kudos',
         '{subject_line}',
         placeholders=('actor', 'subject_line', 'name'),
         link_label='See it on AJi Taqdeer'),

    Case('kudos.declined', 'kudos',
         'Kudos not published',
         'HR declines Kudos that was waiting',
         'The person who sent it',
         'Your Kudos was not published',
         'HR reviewed it and did not publish it. Ask HR if you would like to '
         'know why.',
         placeholders=('name', 'recipient', 'subject_line'),
         link_label='Open AJi Taqdeer'),

    # ── Recognition feed ─────────────────────────────────────────────────
    Case('recognition.certification_recorded', 'recognition',
         'Certification recorded',
         'HR records a certification for you and publishes it',
         'The person who earned it',
         'Your {certification} certification is on AJi Taqdeer',
         '{announcement}',
         placeholders=('name', 'certification', 'announcement', 'actor'),
         link_label='See it on AJi Taqdeer'),

    Case('recognition.liked', 'recognition',
         'Somebody liked your recognition',
         'Somebody likes a recognition that is about you',
         'The person the recognition is about',
         '{actor} liked your recognition',
         '{subject_line}',
         placeholders=('actor', 'subject_line', 'name'),
         link_label='Open AJi Taqdeer'),

    Case('recognition.commented', 'recognition',
         'Somebody commented on your recognition',
         'Somebody comments on a recognition that is about you',
         'The person it is about, unless they were named with @',
         '{actor} commented on your recognition',
         '{comment}',
         placeholders=('actor', 'comment', 'name'),
         link_label='Open AJi Taqdeer'),

    Case('recognition.mentioned', 'recognition',
         'Mentioned in a recognition comment',
         'Somebody names you with @ in a comment, or edits one so it does',
         'Everyone named with @',
         '{actor} mentioned you in a comment',
         '{comment}',
         placeholders=('actor', 'comment', 'name'),
         link_label='Open AJi Taqdeer'),

    # ── AJi News ─────────────────────────────────────────────────────────
    Case('news.mentioned', 'news',
         'Mentioned in a news comment',
         'Somebody names you with @ in a comment, or edits one so it does',
         'Everyone named with @',
         '{actor} mentioned you in a comment',
         '{comment}',
         placeholders=('actor', 'comment', 'name'),
         link_label='Open the article'),

    # ── AJi Exchange ─────────────────────────────────────────────────────
    Case('exchange.mentioned', 'exchange',
         'Mentioned in a thread reply',
         'Somebody names you with @ in a reply, or edits one so it does',
         'Everyone named with @',
         '{actor} mentioned you in {thread}',
         '{comment}',
         placeholders=('actor', 'thread', 'comment', 'name'),
         link_label='Open the thread'),

    # ── Messaging ────────────────────────────────────────────────────────
    Case('chat.mentioned', 'chat',
         'Mentioned in a chat message',
         'Somebody names you with @ in a conversation you are in',
         'Everyone named with @',
         '{actor} mentioned you',
         '{message}',
         placeholders=('actor', 'message', 'where', 'name'),
         link_label='Open the conversation'),

    Case('chat.message_while_away', 'chat',
         'Chat message while you are away',
         # Kept to one line: `when` is a row in a list of thirty-eight, and the
         # one description long enough to wrap made its row taller than every
         # other. The caveat belongs on the detail page, which has `note` for it.
         'A message arrives while you are not on the Hub',
         'Each offline participant who has chat email on',
         'Message from {actor}',
         '{message}',
         placeholders=('actor', 'message', 'where', 'name'),
         link_label='Open the conversation',
         note='Off for everybody unless a reader switches it on for themselves '
              'in their own Notification settings. The daily summary below is '
              'the sane way to hear about chat by email.'),

    Case('chat.daily_digest', 'chat',
         'Daily unread summary',
         'Once a day, at the hour each reader chose',
         'Anyone with the digest on who has unread messages',
         'You have {unread_count}',
         'From {conversation_count}:',
         placeholders=('name', 'unread_count', 'conversation_count'),
         link_label='Open your messages',
         footer='This is your daily summary. You can change the time it '
                'arrives, or turn it off, in Notification settings.',
         note='The list of senders is built from the reader’s own unread '
              'messages and is added under the body.'),

    # ── Room booking ─────────────────────────────────────────────────────
    Case('rooms.invited', 'rooms',
         'Meeting invitation',
         'You are added to a meeting as an attendee',
         'Each person newly added',
         'Meeting invitation: {meeting}',
         '{actor} invited you ({attendance}) to {when}. Open Room Booking to '
         'reply.',
         placeholders=('name', 'actor', 'attendance', 'meeting', 'when'),
         link_label='Open Room Booking'),

    Case('rooms.uninvited', 'rooms',
         'Taken off a meeting',
         'You are removed from a meeting that still goes ahead',
         'Each person taken off the list',
         'Meeting cancelled for you: {meeting}',
         'You are no longer invited to {when}.',
         placeholders=('name', 'meeting', 'when', 'actor'),
         link_label='Open Room Booking'),

    Case('rooms.cancelled', 'rooms',
         'Meeting cancelled',
         'A meeting you were invited to is cancelled',
         'Every attendee',
         'Meeting cancelled: {meeting}',
         '{when} is no longer happening.',
         placeholders=('name', 'meeting', 'when', 'actor'),
         link_label='Open Room Booking'),

    Case('rooms.responded', 'rooms',
         'An attendee replied',
         'An attendee accepts or declines',
         'Whoever booked the room',
         '{actor} {response}: {meeting}',
         '{when}. {summary}',
         placeholders=('actor', 'response', 'meeting', 'when', 'summary', 'name'),
         link_label='Open Room Booking'),

    # ── Training ─────────────────────────────────────────────────────────
    # These reach the bell today and have never been emailed, because they were
    # written before the notification service existed and create their row
    # directly. Declared here so the wording is editable in the same place as
    # everything else, and so switching them on is a decision rather than a
    # rewrite.
    Case('training.submitted', 'training',
         'Training request submitted',
         'You submit a training request',
         'You',
         'Your training request has been submitted',
         'Your request for {course} has been submitted.',
         placeholders=('name', 'course'),
         link_label='Open the request',
         enabled_by_default=False),

    Case('training.needs_manager', 'training',
         'Training request needs a manager',
         'A request reaches the manager who has to approve it',
         'Your manager',
         'A training request needs your approval',
         '{actor} asked to attend {course}.',
         placeholders=('name', 'actor', 'course'),
         link_label='Open the request',
         enabled_by_default=False),

    Case('training.needs_hr', 'training',
         'Training request needs HR',
         'A request reaches HR',
         'HR',
         'A training request needs HR approval',
         '{actor} asked to attend {course}.',
         placeholders=('name', 'actor', 'course'),
         link_label='Open the request',
         enabled_by_default=False),

    Case('training.manager_decided', 'training',
         'Your manager decided',
         'Your manager approves, declines or returns the request',
         'The person who asked',
         'Your manager has responded to your training request',
         '{outcome}',
         placeholders=('name', 'course', 'outcome'),
         link_label='Open the request',
         enabled_by_default=False),

    Case('training.hr_decided', 'training',
         'HR decided',
         'HR approves, declines or asks for more detail',
         'The person who asked',
         'HR has responded to your training request',
         '{outcome}',
         placeholders=('name', 'course', 'outcome'),
         link_label='Open the request',
         enabled_by_default=False),

    Case('training.scheduled', 'training',
         'Training scheduled',
         'HR confirms the dates',
         'The person attending',
         'Your training is scheduled',
         '{course} is scheduled. {when}',
         placeholders=('name', 'course', 'when'),
         link_label='Open the request',
         enabled_by_default=False),

    Case('training.completed', 'training',
         'Training marked complete',
         'HR marks the training complete',
         'The person who attended',
         'Your training is marked complete',
         '{course} has been marked complete. Well done.',
         placeholders=('name', 'course'),
         link_label='Open the request',
         enabled_by_default=False),

    Case('training.commented', 'training',
         'Comment on a training request',
         'Anybody comments on a request',
         'The employee, the manager and the HR owner, except whoever wrote it',
         'New comment on a training request',
         '{comment}',
         placeholders=('name', 'actor', 'course', 'comment'),
         link_label='Open the request',
         enabled_by_default=False),

    # ── Administration ───────────────────────────────────────────────────
    Case('admin.test', 'admin',
         'Test email',
         'An administrator presses Send test email',
         'Whatever address they typed',
         'Test email',
         'This is a test message from AJi Hub. If you are reading it, the Hub '
         'can send email.',
         placeholders=('name',),
         link_label='Open AJi Hub',
         note='Sent on demand from the IT portal, so it ignores the switch.'),
]

CASES_BY_CODE = {c.code: c for c in CASES}


def case(code):
    """The declared case, or None. Never raises: an email must not fail because
    somebody renamed a code."""
    found = CASES_BY_CODE.get(code)
    if found is None:
        logger.error('[email_cases] no such case: %s', code)
    return found


def by_module(module=None, query=''):
    """Cases for the IT portal's list, filtered the way the page filters them."""
    rows = CASES
    if module:
        rows = [c for c in rows if c.module == module]
    if query:
        q = query.strip().lower()
        rows = [c for c in rows
                if q in c.label.lower() or q in c.when.lower()
                or q in c.code.lower() or q in c.module_label.lower()]
    return rows


def module_counts():
    """How many cases each module holds, for the filter's labels."""
    counts = {}
    for c in CASES:
        counts[c.module] = counts.get(c.module, 0) + 1
    return counts


# ── the dash rule ────────────────────────────────────────────────────────
#: Em dash, en dash, horizontal bar, minus sign, and the two-hyphen shorthand
#: that a word processor turns into one. All banned from anything the Hub emails.
_DASHES = re.compile(r'\s*(?:—|–|―|−|--)\s*')


def no_dashes(text):
    """Strip dashes out of anything bound for an inbox.

    A house rule, applied on the way in *and* on the way out. On the way in so
    the editor cannot save one; on the way out because the defaults, the
    placeholder values and the settings-driven wording all arrive from
    elsewhere, and every one of those is a route a dash could take into a
    subject line.

    Replaced with a comma and a space rather than a hyphen: an em dash is doing
    a comma's job in nearly every line it appears in, and "Q1 2026 - the
    citation" reads like a stray keystroke where "Q1 2026, the citation" reads
    like a sentence. A dash already sitting between two digits is left alone, so
    "11:45 - 12:45" and "2026-08-18" survive.
    """
    if not text:
        return text
    out = str(text)

    def swap(match):
        start, end = match.span()
        before = out[start - 1] if start else ''
        after = out[end] if end < len(out) else ''
        if before.isdigit() and after.isdigit():
            return ' to '
        return ', ' if before and after else ' '

    # A dash at an edge is replaced with a space rather than a comma by `swap`
    # above, so there is nothing to tidy here beyond the space itself. Stripping
    # commas as well, which this used to do, ate the one in "Hello Lina," and
    # turned every salutation in the Hub into a sentence fragment.
    return _DASHES.sub(swap, out).strip()


# ── resolving a case into an actual email ────────────────────────────────

class Resolved:
    """One case, with the administrator's overrides applied and the placeholders
    filled in. `enabled` False means send nothing."""

    def __init__(self, case_, enabled, subject, body_lines, link_label,
                 high_importance, attachment, footer):
        self.case = case_
        self.enabled = enabled
        self.subject = subject
        self.body_lines = body_lines
        self.link_label = link_label
        self.high_importance = high_importance
        self.attachment = attachment
        self.footer = footer

    @property
    def body(self):
        return '\n'.join(self.body_lines)


def template_for(code):
    """The stored overrides for a case, or None. Never raises: the table not
    being migrated yet must not stop an email."""
    try:
        from .models import EmailTemplate
        return EmailTemplate.objects.filter(code=code).first()
    except Exception as exc:                                   # noqa: BLE001
        logger.error('[email_cases] could not read the template for %s: %s',
                     code, exc)
        return None


def fill(text, values):
    """Substitute {placeholders}, leaving anything unrecognised visible.

    `str.format` raises on an unknown key and on a stray brace, and an
    administrator experimenting in a text box will produce both. Neither should
    stop an email going out, so unknown names are left as they were written and
    show up in the preview instead.
    """
    if not text:
        return ''

    def one(match):
        key = match.group(1)
        if key in values:
            got = values[key]
            return '' if got is None else str(got)
        return match.group(0)

    return re.sub(r'\{(\w+)\}', one, str(text))


def resolve(code, values=None, template=None):
    """The case as it should actually be sent, or None if there is no such case.

    `values` fills the placeholders. Pass `template` to preview an unsaved edit;
    otherwise the stored row is read.
    """
    found = case(code)
    if found is None:
        return None
    values = values or {}
    row = template if template is not None else template_for(code)

    subject = (row.subject if row and row.subject else found.subject)
    body = (row.body if row and row.body else found.body)

    # Filled first, then stripped: a dash can arrive inside a placeholder value
    # as easily as inside the wording, and a citation somebody typed in Word is
    # exactly where one comes from.
    subject = no_dashes(fill(subject, values))
    lines = [no_dashes(fill(line, values)) for line in body.splitlines()]
    lines = [ln for ln in lines if ln]

    return Resolved(
        case_=found,
        enabled=(row.enabled if row else found.enabled_by_default),
        subject=subject,
        body_lines=lines,
        link_label=no_dashes(found.link_label),
        high_importance=(row.high_importance if row else found.high_importance),
        attachment=(row.attachment if row else found.attach_by_default),
        footer=no_dashes(found.footer),
    )


# ── example values, for showing an administrator a real sentence ──────────
#: What each placeholder is replaced with in the preview. Real-looking values
#: rather than the placeholder names, because "{award_title}: {period}" tells you
#: nothing about whether a subject line reads well and "The Athar Award: Q1 2026"
#: tells you immediately.
EXAMPLES = {
    'name': 'Lina Al Saree',
    'actor': 'Nael Shunnar',
    'recipient': 'Ahmed Al Droushi',
    'nominee': 'Ahmed Al Droushi',
    'period': 'Q1 2026',
    'award_title': 'The Athar Award',
    'allowance': '3 people',
    'places_left': '2 places',
    'days_left': '3 days',
    'closing': 'Nominations close 14 March.',
    'opening': 'It opens 1 March',
    'citation': 'outstanding delivery on the Riyadh metro package',
    'congratulations': 'Congratulations on a remarkable year.',
    'recognition_line': 'In recognition of',
    'hr_note': 'Please add a line about the delivery date.',
    'subject_line': 'Caught the error before the client did',
    'comment': 'Well deserved, this made a real difference.',
    'certification': 'PMP',
    'announcement': 'Lina Al Saree obtained PMP from PMI.',
    'course': 'FIDIC Contracts',
    'outcome': 'Approved. HR will confirm the dates.',
    'thread': 'Concrete pour sequencing',
    'message': 'Can you take a look at the revised drawings?',
    'where': 'the site team',
    'meeting': 'Design review',
    'when': 'Tuesday 3 March, 10:00 to 11:00, Boardroom',
    'attendance': 'required',
    'response': 'accepted',
    'summary': '3 accepted, 1 declined',
    'unread_count': '4 unread messages',
    'conversation_count': '2 conversations',
}


def examples_for(case_):
    """Sample values for this case's placeholders, for the preview."""
    return {p: EXAMPLES.get(p, p.replace('_', ' ')) for p in case_.placeholders}


def current_text(code, template=None):
    """The wording actually in force, as editable text.

    This is what goes in the edit boxes. It is the override if there is one and
    the default if not, so somebody changing one word changes one word instead of
    retyping the sentence. The braces stay in: they are part of the text being
    edited, and removing one is how you lose the recipient's name.
    """
    found = case(code)
    if found is None:
        return '', ''
    row = template if template is not None else template_for(code)
    subject = row.subject if (row and row.subject) else found.subject
    body = row.body if (row and row.body) else found.body
    return subject, body
