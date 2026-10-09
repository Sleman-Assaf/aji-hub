# messaging/models.py
"""Chat: direct conversations and groups.

Four ideas shape this file.

**A conversation is shared; how you see it is yours.** Pinning, clearing and
"read up to here" are one person's business, so they live on `ChatState` — one
row per person per conversation — not on the conversation itself. That is what
lets you delete a chat without deleting somebody else's copy of it.

**Read state is a timestamp, not a flag.** `Message.read` was a single boolean,
which cannot answer "has everybody in this group of nine seen it?". Each
person's `ChatState.last_read_at` can, and it answers the unread count for
direct chats with the same arithmetic.

**Deleting a chat hides it, it does not destroy it.** `cleared_at` hides
everything said before that moment *from you*, and `hidden` drops the
conversation off your list until somebody says something new — the moment it
matters again, it comes back. Nothing is removed from anybody else's history.

**A group needs one owner, not a committee.** Whoever created it, plus the
Intranet Admins, mirroring how communities work in AJi Exchange.
"""
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import models
from django.db.models import Max, Q
from django.utils import timezone

#: Chat attachments. Smaller than the 35 MB an Exchange thread takes: a chat is
#: for passing something along, and a 35 MB video in a popup is a wrong turn.
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

#: How long after sending something you may still correct it.
#:
#: A message is a record of what was said, and an edit changes what the other
#: person is holding you to. Five minutes covers the reason people actually
#: edit — a typo, a wrong number, a name spelled wrong — and stops there,
#: because an hour later the other person has read it, replied to it and acted
#: on it, and rewriting it then is rewriting the conversation.
#:
#: Measured from `sent_at`, not from the last edit: otherwise editing every
#: four minutes would keep a message open forever.
EDIT_WINDOW = timedelta(minutes=5)

#: How many files one message may carry. Ten is more than anybody sends on
#: purpose and few enough that the grid of them stays readable.
MAX_ATTACHMENTS = 10

#: And the whole message together, because ten files each just under the single
#: limit is a quarter of a gigabyte arriving in one request.
MAX_MESSAGE_BYTES = 60 * 1024 * 1024

#: The six offered on a long-press, as WhatsApp does. Anything from the picker
#: is also accepted; this is only what appears without asking.
QUICK_REACTIONS = ['👍', '❤️', '😂', '😮', '😢', '🙏']


class Conversation(models.Model):
    class Kind(models.TextChoices):
        DIRECT = 'direct', 'Direct'
        GROUP = 'group', 'Group'

    participants = models.ManyToManyField(User, related_name='conversations')
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.DIRECT)
    #: Groups only. A direct chat is named after whoever you are talking to.
    name = models.CharField(max_length=120, blank=True)
    #: What the group is for. A name alone cannot distinguish two groups called
    #: "Site" or say whether "Tender review" is the weekly meeting or one bid, and
    #: somebody added to a group months later has no other way to find out.
    description = models.CharField(max_length=280, blank=True)
    photo = models.ImageField(upload_to='messages/groups/', blank=True, null=True)
    #: The group's owner: may rename it, and add or remove people. Null on the
    #: direct conversations that existed before groups, which is harmless
    #: because a direct chat has nothing to own.
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                   blank=True, related_name='conversations_created')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']

    def __str__(self):
        if self.is_group:
            return f'Group: {self.name}'
        names = ', '.join(p.get_full_name() or p.username
                          for p in self.participants.all()[:3])
        return f"Conversation: {names}"

    @property
    def is_group(self):
        return self.kind == self.Kind.GROUP

    def other_participant(self, user):
        return self.participants.exclude(pk=user.pk).first()

    def title_for(self, user):
        """What this conversation is called, from one person's side."""
        if self.is_group:
            return self.name or 'Group'
        other = self.other_participant(user)
        return (other.get_full_name() or other.username) if other else 'Chat'

    def is_admin(self, user):
        """May rename the group and change who is in it."""
        if not user or not user.is_authenticated or not self.is_group:
            return False
        from core.models import is_intranet_admin
        return is_intranet_admin(user) or self.created_by_id == user.pk

    # ── per-person state ─────────────────────────────────────────────────

    def state_for(self, user):
        """This person's row, created on first use.

        Created lazily rather than alongside every membership change: a row is
        only interesting once somebody pins, clears or reads something, and
        get_or_create keeps the two paths from disagreeing.
        """
        state, _ = ChatState.objects.get_or_create(conversation=self, user=user)
        return state

    def visible_messages(self, user, state=None):
        """The messages this person can see.

        Two things hide a message from one person without touching anybody
        else's copy: clearing the chat hides everything before that moment, and
        deleting a single message hides just that one.
        """
        qs = self.messages.all()
        state = state if state is not None else self.state_for(user)
        if state.cleared_at:
            qs = qs.filter(sent_at__gt=state.cleared_at)
        return qs.exclude(hidden_for=user)

    def unread_count(self, user, state=None):
        state = state if state is not None else self.state_for(user)
        qs = self.visible_messages(user, state).exclude(sender=user)
        if state.last_read_at:
            qs = qs.filter(sent_at__gt=state.last_read_at)
        return qs.count()

    def last_message(self, user=None, state=None):
        if user is None:
            return self.messages.last()
        return self.visible_messages(user, state).last()

    def slowest_read_mark(self, exclude_user):
        """When the last person here caught up, or None if somebody has not.

        A plain MIN would be wrong: SQL skips nulls, so one person who has never
        opened the chat would be ignored and their colleague's timestamp would
        become "everybody has seen it". Anybody with no row, or a row that has
        never been read, has to hold the whole thing back — that is what makes
        the second tick mean what it says in a group of nine.
        """
        others = list(self.participants.exclude(pk=exclude_user.pk)
                      .values_list('pk', flat=True))
        if not others:
            return None
        marks = dict(ChatState.objects
                     .filter(conversation=self, user_id__in=others)
                     .values_list('user_id', 'last_read_at'))
        if len(marks) < len(others) or any(v is None for v in marks.values()):
            return None
        return min(marks.values())

    def seen_by_everyone(self, message):
        """True when every other participant has read this message."""
        mark = self.slowest_read_mark(message.sender)
        return bool(mark and mark >= message.sent_at)

    def touch(self):
        """Move this conversation to the top of everyone's list."""
        Conversation.objects.filter(pk=self.pk).update(updated_at=timezone.now())


class ChatState(models.Model):
    """One person's private view of one conversation."""
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE,
                                     related_name='states')
    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='chat_states')
    pinned = models.BooleanField(default=False)
    #: Set by "Delete chat": hides this conversation from my list until somebody
    #: says something new, and hides everything said up to now from me only.
    hidden = models.BooleanField(default=False)
    cleared_at = models.DateTimeField(null=True, blank=True)
    last_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [('conversation', 'user')]

    def __str__(self):
        return f'{self.user} → {self.conversation_id}'

    def mark_read(self):
        self.last_read_at = timezone.now()
        self.save(update_fields=['last_read_at'])

    def clear(self):
        """Delete for me: hide the history and drop it off my list."""
        self.cleared_at = timezone.now()
        self.hidden = True
        self.pinned = False
        self.save(update_fields=['cleared_at', 'hidden', 'pinned'])


class Message(models.Model):
    IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp')

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages')
    # Blank when the message is an attachment with no accompanying note.
    text = models.TextField(blank=True)
    attachment = models.FileField(upload_to='messages/%Y/%m/', blank=True, null=True)
    #: The message this one answers, quoted above it. SET_NULL rather than
    #: CASCADE: losing the quoted message must not take the reply with it.
    reply_to = models.ForeignKey('self', on_delete=models.SET_NULL, null=True,
                                 blank=True, related_name='replies')
    edited_at = models.DateTimeField(null=True, blank=True)
    #: "Ayah added Omar to the group" — written by the system, not typed, and
    #: drawn as a centred note rather than a bubble.
    system = models.BooleanField(default=False)
    #: What kind of system note this is, so the chat can draw the right mark
    #: beside it. Blank for everything typed, and for the notes about groups that
    #: read perfectly well as a line of text. Stored rather than guessed from the
    #: wording: matching on "Voice call" in the browser would break the first
    #: time somebody translated it, and a missed call is worth being able to find
    #: in a transcript.
    #: `birthday` is the exception to the note above: it is a real message from
    #: one person to another, marked so the receiving chat window can celebrate
    #: it. Marked rather than recognised from its wording — matching on a cake
    #: emoji would set confetti off over somebody mentioning cake in the kitchen,
    #: and would miss a wish written in Arabic.
    kind = models.CharField(max_length=12, blank=True, choices=[
        ('call', 'Call'),
        ('call_missed', 'Missed call'),
        ('birthday', 'Birthday wish'),
    ])
    #: People who deleted this message from their own view. Deleting is for you
    #: only — nobody can reach into somebody else's copy of a conversation and
    #: take something out of it.
    hidden_for = models.ManyToManyField(User, blank=True,
                                        related_name='hidden_messages')
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['sent_at']

    def __str__(self):
        label = self.text[:60] or (self.filename and f'[{self.filename}]') or '[empty]'
        return f"{self.sender.get_full_name()}: {label}"

    @property
    def filename(self):
        return self.attachment.name.rsplit('/', 1)[-1] if self.attachment else ''

    @property
    def is_image(self):
        return self.filename.lower().endswith(self.IMAGE_SUFFIXES)

    @property
    def filesize(self):
        try:
            kb = self.attachment.size / 1024
        except Exception:
            return ''
        return f'{kb/1024:.1f} MB' if kb >= 1024 else f'{kb:.0f} KB'

    def attachment_label(self):
        """"report.pdf", "2 photos", "4 files" — one line for a list preview.

        Words only. Whoever is drawing this puts an icon in front of it — see
        `last_icon` in messaging/views.py — because an emoji in the string
        renders as whatever the machine has and cannot be sized or coloured with
        the text beside it.

        Not `self.filename`: that only knows about the single old `attachment`,
        so a message sent with four photos and no words previewed as a blank
        line in the conversation list.
        """
        files = self.all_files()
        if not files:
            return ''
        if len(files) == 1:
            return files[0].filename
        images = sum(1 for f in files if f.is_image)
        if images == len(files):
            return f'{images} photos'
        return f'{len(files)} files'

    def all_files(self):
        """Every file on this message, old storage and new, in order.

        The single `attachment` first, because a message that has one is an old
        message and has nothing else. Returns objects with filename/is_image/
        filesize, so a caller cannot tell which table a file came out of.
        """
        out = []
        if self.attachment:
            out.append(_LegacyFile(self.attachment, self))
        out.extend(self.files.all())
        return out

    def can_edit(self, user):
        """Only the author, never a system note, and only for five minutes."""
        return bool(user and user.is_authenticated
                    and self.sender_id == user.pk and not self.system
                    and self.editable_until() > timezone.now())

    def editable_until(self):
        """The moment the Edit button goes away. See EDIT_WINDOW.

        Sent to the browser as well as checked here, so the button can disappear
        on its own rather than waiting for somebody to press it and be told no.
        """
        # `sent_at` is auto_now_add, so it is only unset on a message that has
        # never been saved — which has nothing to edit yet either way.
        return (self.sent_at or timezone.now()) + EDIT_WINDOW

    def edit_seconds_left(self, now=None):
        """Whole seconds of the window remaining; 0 once it has closed."""
        now = now or timezone.now()
        return max(0, int((self.editable_until() - now).total_seconds()))

    def reaction_summary(self, user=None):
        """[{emoji, count, mine}] — grouped, so nine thumbs are one chip."""
        out = {}
        for r in self.reactions.all():
            slot = out.setdefault(r.emoji, {'emoji': r.emoji, 'count': 0, 'mine': False})
            slot['count'] += 1
            if user is not None and r.user_id == getattr(user, 'pk', None):
                slot['mine'] = True
        return sorted(out.values(), key=lambda s: (-s['count'], s['emoji']))


class _LegacyFile:
    """A message's single old `attachment`, wearing MessageFile's clothes.

    So that `all_files()` returns one kind of thing and the view layer never has
    to ask which storage a file came from.
    """
    __slots__ = ('file', '_msg')

    def __init__(self, fieldfile, message):
        self.file = fieldfile
        self._msg = message

    @property
    def filename(self):
        return self._msg.filename

    @property
    def is_image(self):
        return self._msg.is_image

    @property
    def filesize(self):
        return self._msg.filesize


class MessageFile(models.Model):
    """One of the files on a message.

    `Message.attachment` came first and holds exactly one file. Rather than move
    every message that has ever been sent onto a new table, that field stays
    where it is and keeps working, and anything sent from now on puts its files
    here — one row each. `Message.all_files()` reads the two as one list, so
    nothing above this line has to know there are two places to look.

    The order is the order they were picked. Somebody attaching a before and an
    after does not want them arriving the other way round.
    """
    message = models.ForeignKey(Message, on_delete=models.CASCADE,
                                related_name='files')
    file = models.FileField(upload_to='messages/%Y/%m/')
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['position', 'pk']

    def __str__(self):
        return self.filename

    @property
    def filename(self):
        return self.file.name.rsplit('/', 1)[-1] if self.file else ''

    @property
    def is_image(self):
        return self.filename.lower().endswith(Message.IMAGE_SUFFIXES)

    @property
    def filesize(self):
        try:
            kb = self.file.size / 1024
        except Exception:                                      # noqa: BLE001
            # A row whose file has been removed from disk should not take the
            # whole conversation down with it.
            return ''
        return f'{kb/1024:.1f} MB' if kb >= 1024 else f'{kb:.0f} KB'


class Reaction(models.Model):
    """One person's one emoji on one message."""
    message = models.ForeignKey(Message, on_delete=models.CASCADE,
                                related_name='reactions')
    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='chat_reactions')
    emoji = models.CharField(max_length=16)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('message', 'user', 'emoji')]

    def __str__(self):
        return f'{self.emoji} {self.user}'


# Voice calls live in their own module — they are a separate idea built on top of
# conversations, and the file above is long enough. Imported here at the bottom,
# after Conversation and Message exist, so that Django finds them where it looks
# for an app's models. See messaging/call_models.py.
from .call_models import (Call, CallConfig, CallInvite, CallPeer, CallSignal,  # noqa: E402,F401
                          MAX_PARTICIPANTS, MAX_SIGNAL_BYTES,
                          PEER_TIMEOUT, RING_SECONDS)
