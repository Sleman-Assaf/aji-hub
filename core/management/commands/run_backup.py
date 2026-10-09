# core/management/commands/run_backup.py
"""
Take a backup. Meant for a scheduler on the server:

    python manage.py run_backup                    # whatever the portal says
    python manage.py run_backup --parts db         # database only, seconds
    python manage.py run_backup --parts db,config  # and the config zip
    python manage.py run_backup --keep 10          # how many dated copies to keep

With no arguments it does exactly what the IT portal's Backups page is set to —
what to include and how many to keep — so the policy lives in one place and the
scheduled line does not have to be edited when somebody changes their mind. The
portal generates the line to paste, with the right frequency and hour on it.

What it writes, under the backup folder (the project's backups/ by default,
created if it is not there):

    ajihub-db-YYYYMMDD-HHMMSS.sqlite3     a dated copy of the database
    ajihub-config-YYYYMMDD-HHMMSS.zip     settings.py and the requirements
    files/                                a mirror of everything uploaded

The database and the config are dated, so there is a history to roll back to.
The uploaded files are *mirrored* rather than copied dated: they are several
gigabytes, and a dated copy each night would be a new several gigabytes each
night. The mirror copies only what is new or changed, so the first run is slow
and every run after it is quick.

That is a deliberate trade. A mirror gives you last night's files, not last
week's. If a file was deleted a month ago you will not find it here — for that
you want the dated database plus a snapshot at the storage level, which is the
right tool for four gigabytes.

The work itself lives in core/backups.py, shared with the portal's button, so
a backup taken by hand and one taken at 02:00 are the same backup.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Back up the database, the uploaded files and the configuration.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--parts',
            help='Comma-separated: db, files, config. '
                 'Default: whatever the IT portal is set to.')
        parser.add_argument('--db-only', action='store_true',
                            help='Shorthand for --parts db.')
        parser.add_argument('--keep', type=int, default=None,
                            help='How many dated copies to keep. '
                                 'Default: the portal setting, or 10.')
        parser.add_argument('--quiet', action='store_true')

    def handle(self, *args, **opts):
        from core import backups as bk

        parts = None
        if opts['db_only']:
            parts = ['db']
        elif opts['parts']:
            parts = [p.strip() for p in opts['parts'].split(',') if p.strip()]
            unknown = [p for p in parts if p not in bk.PART_KEYS]
            if unknown:
                self.stderr.write(
                    f'Unknown part(s): {", ".join(unknown)}. '
                    f'Choose from {", ".join(bk.PART_KEYS)}.')
                return

        out = bk.perform(parts=parts, keep=opts['keep'], scheduled=True)

        if not opts['quiet']:
            for line in self._lines(out):
                self.stdout.write(line)
        for problem in out['errors']:
            self.stderr.write(f'  {problem}')

    def _lines(self, out):
        yield f"Folder    -> {out['folder']}"
        if out['db_name']:
            yield f"Database  -> {out['db_name']}"
        if out['config_name']:
            yield (f"Config    -> {out['config_name']} "
                   f"({out['config_files']} files)")
        if 'files' in out['parts']:
            yield (f"Files     -> files/  {out['copied']} new or changed, "
                   f"{out['skipped']} already current")
        if out['removed']:
            yield f"Retention -> {out['removed']} old copies removed"
        yield f"Done in {out['seconds']:.0f}s."
