# messaging/call_models.py
"""Voice calls and screen sharing, on top of the conversations chat already has.

A call belongs to a conversation, so there is nothing new to invite anybody to:
the people in the chat are the people the phone rings for, and a group call is a
call in a group conversation. Direct or group is the conversation's business,
not the call's.

Four ideas shape this file.

**The Hub carries the talking about, not the talking.** Browsers speak WebRTC to
each other; what they cannot do is find each other. So the server holds the
call's roster and passes offers, answers and ICE candidates between browsers —
`CallSignal` is a pigeonhole, one row per note, collected by whoever it is
addressed to and thrown away when the call ends. Nothing about the audio itself
reaches Django. On one office network that means a call is two computers and a
switch; see the note on ICE servers in settings for what changes off it.

**A peer is a tab, not a person.** Somebody with the Hub open twice is one
employee and two browsers, and an offer meant for one of them is meaningless to
the other. So `CallPeer` is one browser session in one call, addressed by a
`peer_id` the server issues. Everybody's phone rings; whichever tab answers
becomes the peer.

**Ringing is not a state, it is an absence.** There is no `ringing` column to
keep in step with reality — being rung means the call is live, you are in the
conversation, and you have neither joined it nor said no. That cannot drift out
of date, and a browser that is closed mid-ring leaves nothing behind to correct.

**A call ends when the last person leaves, or when everybody stops answering.**
Nobody's browser can be trusted to say goodbye — a laptop lid closes and the tab
is simply gone — so presence is a heartbeat, not a farewell. A peer that has not
been heard from in `PEER_TIMEOUT` is treated as gone, and a call with nobody in
it is over.
"""
import logging
import secrets
from datetime import timedelta

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone

from .models import Conversation


class CallConfig(models.Model):
    """The admin's calling configuration — one row, JSON, overrides only.

    A single JSON blob rather than a column per knob, because the knobs are
    read together, written together from one page, and gain nothing from
    being queryable individually — while every new knob as a column would be
    a migration. The shape and defaults live in call_config.py; this row
    holds whatever the IT ▸ Call settings page last saved, and deleting it
    is a clean reset to settings.py behaviour.
    """
    data = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL,
                                   null=True, blank=True,
                                   related_name='call_configs_saved')

    def __str__(self):
        return f'Call configuration (updated {self.updated_at:%Y-%m-%d %H:%M})'

#: The signalling trace — same logger as call_views, so the terminal reads as
#: one story. Endings are logged here because Call.end is the single funnel
#: every way of ending (hang-up, sweep, decline, ring-out) passes through.
clog = logging.getLogger('calls')

#: How long an unanswered call rings before it counts as missed. Long enough to
#: get to the keyboard, short enough that a call nobody is near does not sit
#: ringing on the whole team's screens.
RING_SECONDS = 45

#: A peer says it is still here on every sync, roughly once a second. Missing
#: three or four in a row is a browser that has gone, not a slow network.
PEER_TIMEOUT = timedelta(seconds=20)

#: The upper bound on a mesh: everybody sends their microphone to everybody else,
#: so the traffic one person carries grows with the size of the call. Eight is
#: comfortable for audio; a screen share is the expensive part, which is why only
#: one person shares at a time.
MAX_PARTICIPANTS = 8

#: An SDP description is a few kilobytes and an ICE candidate is a line. The cap
#: is far above both, and it is here so that a signalling endpoint cannot be used
#: to write arbitrary amounts into the database.
MAX_SIGNAL_BYTES = 64 * 1024


class Call(models.Model):
    """One conversation's call. At most one is live at a time."""

    class Status(models.TextChoices):
        LIVE = 'live', 'Live'
        ENDED = 'ended', 'Ended'

    class Ending(models.TextChoices):
        HUNG_UP = 'hung_up', 'Everyone left'
        DECLINED = 'declined', 'Declined'
        MISSED = 'missed', 'Not answered'

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE,
                                     related_name='calls')
    started_by = models.ForeignKey(User, on_delete=models.CASCADE,
                                   related_name='calls_started')
    status = models.CharField(max_length=8, choices=Status.choices,
                              default=Status.LIVE)
    #: Carried by the media bridge rather than a browser mesh. Decided once,
    #: when the call starts — was the bridge up? — and never re-decided, so a
    #: bridge restarted mid-call cannot strand half a call in each mode. The
    #: participant limit follows this: MAX_PARTICIPANTS for a mesh, the
    #: MEDIA_BRIDGE setting's max_participants through the bridge.
    via_bridge = models.BooleanField(default=False)
    #: People who said no. Their phone stops ringing; everybody else's does not.
    #: A separate model would be a row per non-event — this is only ever read as
    #: "is this person still being rung?".
    declined_by = models.ManyToManyField(User, blank=True, related_name='calls_declined')
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    ended_reason = models.CharField(max_length=10, choices=Ending.choices, blank=True)

    class Meta:
        ordering = ['-started_at']
        indexes = [models.Index(fields=['status', 'conversation'])]

    def __str__(self):
        return f'Call #{self.pk} in {self.conversation_id} ({self.status})'

    # ── who is here ──────────────────────────────────────────────────────

    def active_peers(self):
        """The browsers currently in this call, stale ones already dropped."""
        self.drop_stale_peers()
        return (self.peers.filter(left_at__isnull=True)
                .select_related('user', 'user__employee').order_by('joined_at'))

    def drop_stale_peers(self):
        """Mark as gone anything that has stopped saying it is here.

        A browser that crashed, or a laptop that was shut, never sends a leave.
        Without this the call would stay live with a roster of ghosts and never
        write its "call ended" line into the chat.
        """
        self.peers.filter(left_at__isnull=True,
                          last_ping__lt=timezone.now() - PEER_TIMEOUT
                          ).update(left_at=timezone.now())

    def joined_user_ids(self):
        return set(self.peers.values_list('user_id', flat=True))

    @property
    def is_group_call(self):
        """Does this call behave like a group call?

        A group conversation's call always does. A direct conversation's call
        does the moment anybody has been added into it — three people on a
        call are a group whatever the chat underneath is, and the rule that a
        direct call ends when one of its two hangs up must stop applying.
        """
        return self.conversation.is_group or self.invites.exists()

    def ring_started_for(self, user):
        """When this person's phone started ringing for this call, or None.

        Conversation participants ring from the call's start. Somebody added
        mid-call rings from the moment of the invitation — their forty-five
        seconds are their own, however old the call is. An invitation also
        wins for a participant who has one: re-inviting somebody who missed
        the first ring is what makes their phone ring again.
        """
        invite = (self.invites.filter(user=user)
                  .order_by('-created_at').first())
        if invite is not None:
            return invite.created_at
        if self.conversation.participants.filter(pk=user.pk).exists():
            return self.started_at
        return None

    def is_ringing_for(self, user):
        """Is this person's phone ringing right now?

        Derived rather than stored, so it cannot be left saying yes by a browser
        that never came back: live, still within this person's own ringing
        window (see ring_started_for), and has neither answered nor declined.
        """
        if self.status != self.Status.LIVE:
            return False
        if self.declined_by.filter(pk=user.pk).exists():
            return False
        if self.peers.filter(user=user).exists():
            return False
        started = self.ring_started_for(user)
        if started is None:
            return False
        return timezone.now() - started <= timedelta(seconds=RING_SECONDS)

    def add_peer(self, user):
        """Put one browser session into the call and hand it its address."""
        return CallPeer.objects.create(call=self, user=user,
                                       peer_id=secrets.token_hex(8))

    # ── ending ───────────────────────────────────────────────────────────

    def end(self, reason=Ending.HUNG_UP):
        """Close the call, tidy up after it, and leave a line in the chat.

        Idempotent: two people hanging up at the same moment, and the sweep that
        ends abandoned calls, all arrive here, and only the first does anything.
        """
        updated = (Call.objects.filter(pk=self.pk, status=self.Status.LIVE)
                   .update(status=self.Status.ENDED, ended_at=timezone.now(),
                           ended_reason=reason))
        if not updated:
            return False
        self.refresh_from_db()
        clog.info('call %d ENDED (%s) after %d s — conv %d, %d peer(s) took '
                  'part', self.pk, self.ended_reason, self.duration_seconds,
                  self.conversation_id, self.peers.count())
        self.peers.filter(left_at__isnull=True).update(left_at=self.ended_at)
        # The pigeonhole only matters while the call is going on. Anything still
        # in it is undeliverable now, and keeping it would grow a table nobody
        # ever reads again.
        self.signals.all().delete()
        self.write_chat_line()
        return True

    @property
    def duration_seconds(self):
        end = self.ended_at or timezone.now()
        return max(0, int((end - self.started_at).total_seconds()))

    def summary(self):
        """The line that goes into the conversation once the call is over."""
        if self.ended_reason in (self.Ending.MISSED, self.Ending.DECLINED):
            return ('Missed voice call' if self.ended_reason == self.Ending.MISSED
                    else 'Voice call declined')
        secs = self.duration_seconds
        if secs < 60:
            length = f'{secs} sec'
        elif secs < 3600:
            length = f'{secs // 60} min'
        else:
            length = f'{secs // 3600} hr {secs % 3600 // 60} min'
        return f'Voice call · {length}'

    def write_chat_line(self):
        """A call leaves a trace in the conversation, the way a message does.

        Written as a system note, so it draws as a centred line rather than a
        bubble from whoever happened to hang up, and so the chat list shows
        "Missed voice call" instead of going quiet about it.
        """
        from .models import Message
        missed = self.ended_reason in (self.Ending.MISSED, self.Ending.DECLINED)
        Message.objects.create(conversation=self.conversation,
                               sender=self.started_by,
                               text=self.summary(), system=True,
                               kind='call_missed' if missed else 'call')
        self.conversation.touch()


class CallPeer(models.Model):
    """One browser session in one call.

    `peer_id` rather than the user id is what signalling is addressed to. Two
    tabs belonging to the same person are two peers with two microphones and two
    sets of ICE candidates, and sending one's answer to the other would leave
    both connections half-built.
    """
    call = models.ForeignKey(Call, on_delete=models.CASCADE, related_name='peers')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='call_peers')
    peer_id = models.CharField(max_length=32)
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)
    muted = models.BooleanField(default=False)
    #: Screen sharing. One person at a time — starting takes it from whoever had
    #: it, because a mesh carries every sharer's screen to every other person.
    sharing = models.BooleanField(default=False)
    #: Last heard from. Presence is a heartbeat: see Call.drop_stale_peers.
    last_ping = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('call', 'peer_id')]
        indexes = [models.Index(fields=['call', 'left_at'])]

    def __str__(self):
        return f'{self.user} @ call {self.call_id}'

    def ping(self):
        CallPeer.objects.filter(pk=self.pk).update(last_ping=timezone.now())

    def leave(self):
        if self.left_at is None:
            self.left_at = timezone.now()
            self.sharing = False
            self.save(update_fields=['left_at', 'sharing'])


class CallSignal(models.Model):
    """One note from one browser to another, waiting to be collected.

    Rows are read by "everything for me newer than what I have already seen", so
    a collected note is not deleted on the way out: a lost response would take
    an offer with it and leave a connection that never completes. They go when
    the call does.
    """

    class Kind(models.TextChoices):
        OFFER = 'offer', 'Offer'
        ANSWER = 'answer', 'Answer'
        ICE = 'ice', 'ICE candidate'
        BYE = 'bye', 'Goodbye'

    call = models.ForeignKey(Call, on_delete=models.CASCADE, related_name='signals')
    from_peer = models.CharField(max_length=32)
    to_peer = models.CharField(max_length=32)
    kind = models.CharField(max_length=8, choices=Kind.choices)
    #: The SDP or the candidate, as the browser produced it. Opaque to Django,
    #: which never parses it — it is handed back to the other browser verbatim.
    payload = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        indexes = [models.Index(fields=['call', 'to_peer', 'id'])]

    def __str__(self):
        return f'{self.kind} {self.from_peer}→{self.to_peer}'


class CallInvite(models.Model):
    """One person asked into a live call from inside it.

    This is what lets any call grow: somebody on the call invites a colleague
    — into a direct call, turning it into a group call, or into a group call
    that is already going — and the colleague's phone rings for RING_SECONDS
    from `created_at`, not from the call's start. The row also stands as the
    invited person's ticket into a call whose conversation they are not a
    member of: access checks accept it alongside conversation membership, for
    this call only. Re-inviting refreshes created_at, which is what makes a
    phone that rang out ring again.
    """
    call = models.ForeignKey(Call, on_delete=models.CASCADE,
                             related_name='invites')
    user = models.ForeignKey(User, on_delete=models.CASCADE,
                             related_name='call_invites')
    invited_by = models.ForeignKey(User, on_delete=models.SET_NULL,
                                   null=True, blank=True,
                                   related_name='call_invites_sent')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['call', 'user'],
                                               name='one_call_invite_per_person')]

    def __str__(self):
        return f'Invite: {self.user} into call #{self.call_id}'
