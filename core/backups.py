# core/backups.py
"""Taking a backup, in one place.

The IT portal's button and `manage.py run_backup` used to carry their own copy
of this: two implementations of "copy the database", one of which knew about
retention and one of which did not. So a backup taken by hand never pruned
anything, and the two disagreed about what a backup even contained.

One engine now, and both callers hand it the same three questions:

    what to include   any of db / files / config, chosen in the portal
    where to put it   core.models.backup_dir(), the project's backups/ folder
    how many to keep  the newest N of each dated artefact, default 10

Nothing here raises at the caller. A backup that fails half way is reported as
what it managed, so an unreachable media folder does not cost you the database
copy that had already been written.
"""
import os
import shutil
import time
import zipfile

from django.conf import settings
from django.utils import timezone

from .models import BackupRecord, GlobalSetting, ITAuditLog, backup_dir

#: What a backup can contain. The keys are stored in a GlobalSetting, so they
#: are part of the data — renaming one silently changes what gets backed up.
PARTS = [
    ('db', 'Database (SQL)',
     'Everything the Hub knows: people, pages, settings, messages, the log.'),
    ('files', 'Uploaded files',
     'Photos, documents, brand files — everything under media.'),
    ('config', 'Configuration',
     'settings.py and the pinned requirements — what you need to stand the '
     'Hub up again on another machine.'),
]
PART_KEYS = [key for key, _label, _blurb in PARTS]
DEFAULT_PARTS = ['db', 'files']

#: How often the scheduled job should run. The Hub cannot run it itself — see
#: schedule_command() — so this drives the line the operator pastes, and is the
#: record of what was intended.
SCHEDULES = [
    ('off', 'Not scheduled', 'Only when somebody presses the button.'),
    ('daily', 'Every day', 'The usual choice. One copy a night.'),
    ('weekly', 'Every week', 'Mondays.'),
    ('monthly', 'Every month', 'The 1st.'),
]
SCHEDULE_KEYS = [key for key, _label, _blurb in SCHEDULES]

KEY_PARTS = 'backup_parts'
KEY_SCHEDULE = 'backup_schedule'
KEY_HOUR = 'backup_hour'
KEY_KEEP = 'backup_keep'
KEY_LAST_RUN = 'backup_last_run'

#: Ten dated copies is roughly a fortnight of nightly backups, which is the
#: window in which somebody notices what went wrong.
DEFAULT_KEEP = 10
DEFAULT_HOUR = 2


# ─────────────────────────────────────────────────────────────────────────────
#  what the operator chose
# ─────────────────────────────────────────────────────────────────────────────

def chosen_parts():
    """The parts to include, as stored. Never empty — a backup of nothing is
    not a backup, so an empty setting falls back to the default."""
    raw = GlobalSetting.get(KEY_PARTS, '')
    parts = [p for p in raw.split(',') if p in PART_KEYS]
    return parts or list(DEFAULT_PARTS)


def chosen_schedule():
    value = GlobalSetting.get(KEY_SCHEDULE, '') or 'daily'
    return value if value in SCHEDULE_KEYS else 'daily'


def chosen_hour():
    raw = GlobalSetting.get(KEY_HOUR, '')
    return int(raw) if str(raw).isdigit() and 0 <= int(raw) <= 23 else DEFAULT_HOUR


def keep_count():
    raw = GlobalSetting.get(KEY_KEEP, '')
    return int(raw) if str(raw).isdigit() and int(raw) > 0 else DEFAULT_KEEP


# ─────────────────────────────────────────────────────────────────────────────
#  the pieces
# ─────────────────────────────────────────────────────────────────────────────

def _stamp():
    now = timezone.now()
    if timezone.is_aware(now):
        now = timezone.localtime(now)
    return now, now.strftime('%Y%m%d-%H%M%S')


def copy_database(folder, stamp):
    """A dated copy of the database. Returns (filename, bytes).

    sqlite3's own backup API rather than a file copy: a copy taken while
    something is mid-write is a copy that may not open again.
    """
    db_path = settings.DATABASES['default'].get('NAME', '')
    if not db_path or not os.path.exists(db_path):
        raise FileNotFoundError('No database file to copy.')
    name = f'ajihub-db-{stamp}.sqlite3'
    dest = os.path.join(folder, name)
    try:
        import sqlite3
        source = sqlite3.connect(f'file:{db_path}?mode=ro', uri=True)
        target = sqlite3.connect(dest)
        with target:
            source.backup(target)
        source.close()
        target.close()
    except Exception:
        shutil.copy2(db_path, dest)
    return name, os.path.getsize(dest)


#: What "configuration" means here: the files you need to stand the Hub up
#: again somewhere else. Deliberately not the operator settings — the mail,
#: LDAP and WhatsApp credentials live in the database and are already inside
#: the db part, and writing them to a second file on disk would be one more
#: place to leak them from.
CONFIG_FILES = [
    os.path.join('aji_hub', 'settings.py'),
    os.path.join('aji_hub', 'urls.py'),
    'requirements.txt',
    'requirements.lock.txt',
    'manage.py',
]


def copy_config(folder, stamp):
    """The configuration files, as one small dated zip. Returns (name, bytes)."""
    root = str(settings.BASE_DIR)
    name = f'ajihub-config-{stamp}.zip'
    dest = os.path.join(folder, name)
    written = 0
    with zipfile.ZipFile(dest, 'w', zipfile.ZIP_DEFLATED) as zf:
        for rel in CONFIG_FILES:
            full = os.path.join(root, rel)
            if os.path.exists(full):
                zf.write(full, rel.replace('\\', '/'))
                written += 1
    return name, os.path.getsize(dest), written


def mirror_media(folder):
    """Copy anything new or changed under media into files/.

    Mirrored rather than copied dated: media is gigabytes, and a dated copy
    each night would be a new several gigabytes each night. Compared on size
    and modification time rather than by hashing — hashing gigabytes every
    night to find the handful of files that changed costs more than the copy
    it saves.

    Never deletes from the mirror, so it holds last night rather than last
    month. A file deleted weeks ago is not in here; for that you want a
    snapshot at the storage level, which is the right tool for four gigabytes.
    """
    media_root = getattr(settings, 'MEDIA_ROOT', '')
    if not media_root or not os.path.isdir(media_root):
        return 0, 0, 0
    target = os.path.join(folder, 'files')
    os.makedirs(target, exist_ok=True)
    copied = skipped = total = 0
    for root, _dirs, files in os.walk(media_root):
        rel = os.path.relpath(root, media_root)
        out_dir = target if rel == '.' else os.path.join(target, rel)
        os.makedirs(out_dir, exist_ok=True)
        for f in files:
            src_f = os.path.join(root, f)
            dst_f = os.path.join(out_dir, f)
            try:
                s = os.stat(src_f)
                total += s.st_size
                if os.path.exists(dst_f):
                    d = os.stat(dst_f)
                    if d.st_size == s.st_size and int(d.st_mtime) >= int(s.st_mtime):
                        skipped += 1
                        continue
                shutil.copy2(src_f, dst_f)
                copied += 1
            except OSError:
                continue
    return copied, skipped, total


#: The dated artefacts retention applies to, as (prefix, suffix). The media
#: mirror is not in here: it is one folder that is updated in place, so there
#: is nothing dated to prune.
DATED = [('ajihub-db-', '.sqlite3'), ('ajihub-config-', '.zip')]


def prune(folder, keep):
    """Keep the newest `keep` of each dated artefact; remove the rest.

    The filenames sort chronologically because the stamp is YYYYMMDD-HHMMSS,
    so newest-first is a reverse sort and needs no stat call per file.
    """
    if keep <= 0:
        return 0
    removed = 0
    try:
        listing = os.listdir(folder)
    except OSError:
        return 0
    for prefix, suffix in DATED:
        dated = sorted((f for f in listing
                        if f.startswith(prefix) and f.endswith(suffix)),
                       reverse=True)
        for old in dated[keep:]:
            try:
                os.remove(os.path.join(folder, old))
                BackupRecord.objects.filter(filename=old).delete()
                removed += 1
            except OSError:
                continue
    return removed


# ─────────────────────────────────────────────────────────────────────────────
#  the whole thing
# ─────────────────────────────────────────────────────────────────────────────

def perform(parts=None, keep=None, note='', user=None, request=None,
            scheduled=False):
    """Take a backup of `parts`. Returns a summary; never raises.

    The folder is created here if it is not there, which is what makes
    "somebody deleted backups/" a non-event: the next backup puts it back.
    """
    parts = [p for p in (parts or chosen_parts()) if p in PART_KEYS]
    keep = keep if keep is not None else keep_count()
    folder = backup_dir()              # created if it is not there yet
    now, stamp = _stamp()
    started = time.time()

    out = {'folder': folder, 'parts': parts, 'errors': [], 'db_name': '',
           'db_bytes': 0, 'config_name': '', 'config_files': 0,
           'copied': 0, 'skipped': 0, 'removed': 0, 'seconds': 0}

    if 'db' in parts:
        try:
            out['db_name'], out['db_bytes'] = copy_database(folder, stamp)
        except Exception as exc:                                # noqa: BLE001
            out['errors'].append(f'database: {exc}')

    if 'config' in parts:
        try:
            name, size, count = copy_config(folder, stamp)
            out['config_name'], out['config_files'] = name, count
        except Exception as exc:                                # noqa: BLE001
            out['errors'].append(f'configuration: {exc}')

    if 'files' in parts:
        try:
            out['copied'], out['skipped'], _bytes = mirror_media(folder)
        except Exception as exc:                                # noqa: BLE001
            out['errors'].append(f'files: {exc}')

    out['removed'] = prune(folder, keep)
    out['seconds'] = time.time() - started

    # One row per backup, named for the database copy when there is one — that
    # is the artefact somebody downloads. A config-only backup is named for its
    # zip instead, so every row still points at a real file.
    filename = out['db_name'] or out['config_name']
    if filename:
        BackupRecord.objects.create(
            filename=filename,
            size_bytes=out['db_bytes'] or 0,
            kind='+'.join(parts)[:20],
            note=(note or summary_line(out, scheduled))[:200],
            taken_by=user if (user and getattr(user, 'is_authenticated', False)) else None)
        ITAuditLog.write(request, ITAuditLog.Action.BACKUP,
                         ('Scheduled backup: ' if scheduled else 'Backup taken: ')
                         + summary_line(out, scheduled), target=filename)
        GlobalSetting.set(KEY_LAST_RUN, now.strftime('%Y-%m-%d %H:%M'), None)
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  putting one back
# ─────────────────────────────────────────────────────────────────────────────

class RestoreError(Exception):
    """The restore did not happen, and the live database was not touched."""


def restorable(filename):
    """Only a dated database copy can be put back.

    A config zip is code, not data: unzipping settings.py over a running
    install is a deployment, not a restore, so it is offered as a download
    instead. The media mirror is a folder rather than a dated artefact.
    """
    return bool(filename) and filename.endswith('.sqlite3')


def _looks_like_a_hub_database(path):
    """Cheap sanity check before overwriting everything.

    Not integrity_check: on a real database that reads every page, and the
    failure it guards against here is somebody pointing this at the wrong file
    — which a missing table catches instantly.
    """
    import sqlite3
    try:
        con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            names = {r[0] for r in con.execute(
                "select name from sqlite_master where type='table'")}
        finally:
            con.close()
    except Exception as exc:                                     # noqa: BLE001
        raise RestoreError(f'That file is not a readable database ({exc}).')
    missing = {'django_migrations', 'auth_user'} - names
    if missing:
        raise RestoreError('That file is a database, but not one of the Hub’s '
                           f'— it has no {", ".join(sorted(missing))} table.')
    return True


def restore(filename, user=None, request=None):
    """Put a dated database copy back, over the live database.

    The order matters and is the whole safety of this:

    1. the file is checked before anything is touched;
    2. the *current* database is copied first, so a restore can be undone;
    3. connections are closed, and only then is the live file written.

    The copy taken at step 2 is recorded **after** the restore, not before —
    a row written before would be inside the database that step 3 overwrites,
    so the safety copy would exist on disk with nothing pointing at it.

    Written into the existing file with sqlite3's backup API rather than
    copied over it: on Windows a file another process has open cannot be
    replaced, and the running Hub has this one open.
    """
    folder = backup_dir()
    safe_name = os.path.basename(filename or '')
    source_path = os.path.join(folder, safe_name)

    if not restorable(safe_name):
        raise RestoreError('Only a dated database copy can be restored.')
    if not os.path.exists(source_path):
        raise RestoreError('That backup is no longer on disk.')
    _looks_like_a_hub_database(source_path)

    db_path = settings.DATABASES['default'].get('NAME', '')
    if not db_path or not os.path.exists(db_path):
        raise RestoreError('The live database could not be found.')

    now, stamp = _stamp()
    try:
        safety_name, safety_bytes = copy_database(folder, stamp)
    except Exception as exc:                                     # noqa: BLE001
        raise RestoreError(f'Could not copy the current database first, so '
                           f'nothing was restored ({exc}).')

    import sqlite3
    from django.db import connections
    connections.close_all()
    try:
        source = sqlite3.connect(f'file:{source_path}?mode=ro', uri=True)
        target = sqlite3.connect(db_path)
        try:
            with target:
                source.backup(target)
        finally:
            source.close()
            target.close()
    except Exception as exc:                                     # noqa: BLE001
        raise RestoreError(
            f'The restore failed part way ({exc}). The copy taken first is '
            f'{safety_name} — hand it to whoever maintains the Hub.')
    finally:
        connections.close_all()

    # From here on we are talking to the restored database.
    BackupRecord.objects.create(
        filename=safety_name, size_bytes=safety_bytes, kind='db',
        note=f'Taken automatically before restoring {safe_name}.',
        taken_by=user if (user and getattr(user, 'is_authenticated', False)) else None)
    ITAuditLog.write(request, ITAuditLog.Action.BACKUP,
                     f'Database restored from {safe_name}; the database as it '
                     f'was is {safety_name}', target=safe_name)
    return {'restored': safe_name, 'safety': safety_name,
            'safety_bytes': safety_bytes}


def summary_line(out, scheduled=False):
    """One sentence describing what a run actually did."""
    bits = []
    if out['db_name']:
        bits.append('database')
    if out['config_name']:
        bits.append(f"config ({out['config_files']} files)")
    if 'files' in out['parts']:
        bits.append(f"{out['copied']} file(s) copied, {out['skipped']} already current")
    if out['removed']:
        bits.append(f"{out['removed']} old copies removed")
    text = ', '.join(bits) or 'nothing'
    if out['errors']:
        text += ' — failed: ' + '; '.join(out['errors'])
    return f"{text}, {out['seconds']:.0f}s"


# ─────────────────────────────────────────────────────────────────────────────
#  the line the operator has to paste
# ─────────────────────────────────────────────────────────────────────────────

def schedule_command(every=None, hour=None, parts=None, project=None,
                     python=None, on_windows=None):
    """The scheduler line matching what was chosen, for both platforms.

    The Hub cannot schedule itself: a web application only runs when somebody
    asks it for a page. So the choice made in the portal is recorded here and
    turned into the exact line for the server's own scheduler, with the parts
    already on it — the setting and the command cannot drift apart because the
    command is generated from the setting.
    """
    import sys
    every = every or chosen_schedule()
    hour = chosen_hour() if hour is None else hour
    parts = parts or chosen_parts()
    project = project or str(settings.BASE_DIR)
    python = python or sys.executable
    if on_windows is None:
        on_windows = os.name == 'nt'

    flags = '' if sorted(parts) == sorted(DEFAULT_PARTS) else \
        ' --parts ' + ','.join(parts)
    command = f'run_backup{flags}'

    win_when = {
        'daily': f'/sc DAILY /st {hour:02d}:00',
        'weekly': f'/sc WEEKLY /d MON /st {hour:02d}:00',
        'monthly': f'/sc MONTHLY /d 1 /st {hour:02d}:00',
    }.get(every, f'/sc DAILY /st {hour:02d}:00')
    cron_when = {
        'daily': f'0 {hour} * * *',
        'weekly': f'0 {hour} * * 1',
        'monthly': f'0 {hour} 1 * *',
    }.get(every, f'0 {hour} * * *')

    # /tr takes a single argument, so the program and its arguments have to be
    # quoted inside the quoted value; without the backslashes the command ends
    # at the first quote. /f overwrites a task of the same name, so re-running
    # this is safe.
    inner = f'\\"{python}\\" \\"{os.path.join(project, "manage.py")}\\" {command}'
    linux_path = project.replace('\\', '/') if not on_windows else '/srv/ajihub'

    return {
        'every': every,
        'hour': hour,
        'windows': f'schtasks /create /tn "AJi Hub backup" {win_when} '
                   f'/tr "{inner}" /f',
        'linux': f'{cron_when} cd {linux_path} && '
                 f'{linux_path}/.venv/bin/python manage.py {command}',
        'linux_path': linux_path,
        'on_windows': on_windows,
        'linux_is_placeholder': on_windows,
    }
