# core/signals.py
"""What goes into the audit log without anybody writing a line to put it there.

The IT portal's own screens call ITAuditLog.write directly. Everything here is
the rest of the Hub: who signed in, who was refused, and every request that
moved from one stage to the next.

Connected in CoreConfig.ready(). Kept in one file so "what is recorded" has a
single answer rather than being scattered across the apps that happen to raise
the events.
"""
import logging

from django.contrib.auth.signals import (user_logged_in, user_logged_out,
                                         user_login_failed)
from django.db.backends.signals import connection_created
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

log = logging.getLogger(__name__)


# ── SQLite tuning ───────────────────────────────────────────────────────────

@receiver(connection_created)
def _sqlite_pragmas(sender, connection, **kwargs):
    """Put SQLite into WAL mode on every connection the Hub opens.

    The default rollback journal fsyncs the whole database file on every
    commit and blocks all readers while a write is in progress — which is why
    the call stress test measured single writes taking whole seconds on the
    Windows disk. WAL appends to a log instead, readers never wait for
    writers, and `synchronous=NORMAL` (safe in WAL: a crash can lose the last
    moments, never corrupt) skips the fsync-per-commit. Together these are the
    difference between tens and hundreds of signalling operations a second on
    the same hardware.

    A signal rather than settings.py because Django 5.0's SQLite backend has
    no `init_command` option — that arrived in 5.1 — and PRAGMAs are
    per-connection, so they must run on each one. Harmless to re-run; ignored
    entirely on any other database engine.
    """
    if connection.vendor != 'sqlite':
        return
    cursor = connection.cursor()
    # WAL is a property of the database file, but asking every time costs
    # nothing and covers a file restored from a backup made in journal mode.
    cursor.execute('PRAGMA journal_mode=WAL;')
    cursor.execute('PRAGMA synchronous=NORMAL;')
    # How long a writer waits for the lock before giving up with "database is
    # locked". Ten seconds, because a queue that clears is better than an
    # error that surfaces as a failed heartbeat mid-call.
    cursor.execute('PRAGMA busy_timeout=10000;')
    # Pages kept in this connection's cache, negative = KiB. 64 MB keeps the
    # hot tables (peers, signals, messages) out of the disk entirely.
    cursor.execute('PRAGMA cache_size=-65536;')
    # Temp indices and sorts in RAM rather than in files beside the database.
    cursor.execute('PRAGMA temp_store=MEMORY;')


def _ip(request):
    if request is None:
        return None
    fwd = request.META.get('HTTP_X_FORWARDED_FOR', '')
    return (fwd.split(',')[0].strip() if fwd else request.META.get('REMOTE_ADDR')) or None


# ── sign-in ─────────────────────────────────────────────────────────────────

@receiver(user_logged_in)
def _signed_in(sender, request, user, **kwargs):
    from .models import ITAuditLog
    ITAuditLog.write(request, ITAuditLog.Action.LOGIN,
                     f'{user.get_full_name() or user.username} signed in',
                     target=user.username)


@receiver(user_logged_out)
def _signed_out(sender, request, user, **kwargs):
    from .models import ITAuditLog
    if user is None:
        return
    ITAuditLog.write(request, ITAuditLog.Action.LOGOUT,
                     f'{user.get_full_name() or user.username} signed out',
                     target=user.username)


@receiver(user_login_failed)
def _sign_in_refused(sender, credentials, request=None, **kwargs):
    """A refused sign-in, with the username tried but never the password.

    Worth recording: a run of these against one account is the shape a
    brute-force attempt makes, and without them there is nothing to notice it
    in. credentials is scrubbed by Django before it reaches here, but the
    username is read explicitly rather than logging the whole dict, so a future
    change to what Django passes cannot start writing secrets into the log.
    """
    from .models import ITAuditLog
    username = (credentials or {}).get('username') or 'unknown'
    ITAuditLog.write(request, ITAuditLog.Action.LOGIN_FAILED,
                     f'Sign-in refused for “{username}”', target=str(username)[:200])


# ── requests moving through their stages ────────────────────────────────────

#: model label -> (field holding the stage, how to describe one)
WORKFLOWS = {
    'training.TrainingRequest': ('stage', 'Training request'),
    'services.JobRequisition': ('status', 'Job requisition'),
    'services.RoomBooking': ('status', 'Room booking'),
}


def _label(instance):
    """A short human description of which request this is."""
    for attr in ('title', 'course_name', 'job_title', 'purpose', 'name'):
        value = getattr(instance, attr, None)
        if value:
            return str(value)[:60]
    return f'#{instance.pk}'


@receiver(pre_save)
def _remember_stage(sender, instance, **kwargs):
    """Read the stage as it is stored, before this save overwrites it."""
    key = f'{sender._meta.app_label}.{sender.__name__}'
    spec = WORKFLOWS.get(key)
    if not spec or instance.pk is None:
        return
    field = spec[0]
    try:
        was = sender.objects.filter(pk=instance.pk).values_list(field, flat=True).first()
        instance._audit_was = was
    except Exception:
        instance._audit_was = None


@receiver(post_save)
def _stage_changed(sender, instance, created, **kwargs):
    key = f'{sender._meta.app_label}.{sender.__name__}'
    spec = WORKFLOWS.get(key)
    if not spec:
        return
    field, noun = spec
    from .models import ITAuditLog

    # Both ends of the move read as the label somebody sees on the screen.
    # Taking the new one from _get_FIELD_display and the old one straight off
    # the column gave "pending_manager → Pending HR Approval", which is the
    # same fact written two different ways in one sentence.
    try:
        choices = dict(sender._meta.get_field(field).choices or [])
    except Exception:
        choices = {}
    show = lambda v: choices.get(v, v)

    now = getattr(instance, field, None)
    if created:
        ITAuditLog.write(None, ITAuditLog.Action.WORKFLOW,
                         f'{noun} raised: {_label(instance)} — {show(now)}',
                         target=f'{key}#{instance.pk}')
        return

    was = getattr(instance, '_audit_was', None)
    if was is None or was == now:
        return
    ITAuditLog.write(None, ITAuditLog.Action.WORKFLOW,
                     f'{noun} {_label(instance)}: {show(was)} → {show(now)}',
                     target=f'{key}#{instance.pk}')


def connect():
    """Imported for the side effect of the decorators above. Here so the app
    config reads as an instruction rather than a bare import."""
    log.debug('[core] audit signals connected')
