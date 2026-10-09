# exchange/management/commands/purge_communities.py
"""Clear out communities that were removed more than a fortnight ago.

    manage.py purge_communities

Removing a community in AJi Exchange hides it and destroys nothing, so that a
mis-click is recoverable. That is the right default and it has an obvious
failure: without something to finish the job, "removed" quietly means "kept for
ever", and the bin grows a tail of communities nobody will ever restore.

This is that something. It destroys communities whose removal is older than the
grace period, and the threads, replies, attachments and join requests inside
them. There is no undo — the grace period is the undo, and it has run out.

Nothing calls this on its own. It runs when the server is told to run it, which
is deliberate: a scheduled task is visible and can be turned off, whereas a
sweep that fires when somebody happens to open a page destroys data at a moment
nobody chose. Until it is scheduled, the bin simply keeps filling and the
"Delete for good" button on /exchange/deleted/ is the way through.

Set it up on the Hub's server with Task Scheduler:

    schtasks /create /tn "AJi Hub community clear-out" /sc daily /st 03:00 ^
      /tr "\"C:\\path\\to\\.venv\\Scripts\\python.exe\" \"C:\\path\\to\\manage.py\" purge_communities"

Flags:
    --dry-run       list what would go, destroy nothing
    --days N        override the grace period (default: Community.PURGE_AFTER_DAYS)
"""
from django.core.management.base import BaseCommand

from exchange.models import Community


class Command(BaseCommand):
    help = ('Permanently deletes communities removed more than '
            f'{Community.PURGE_AFTER_DAYS} days ago.')

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would be deleted and delete nothing.')
        parser.add_argument(
            '--days', type=int, default=None,
            help=f'Grace period in days (default {Community.PURGE_AFTER_DAYS}).')

    def handle(self, *args, **options):
        days = options['days']
        days = Community.PURGE_AFTER_DAYS if days is None else days
        if days < 0:
            self.stderr.write(self.style.ERROR('--days cannot be negative.'))
            return

        dry = options['dry_run']
        # Listed before it happens rather than counted after, so the log says
        # which communities went and somebody can check that against what they
        # expected. A bare "3 deleted" is unverifiable the day it is wrong.
        doomed = list(Community.expired(days=days)
                      .select_related('deleted_by')
                      .order_by('deleted_at'))

        if not doomed:
            self.stdout.write(f'Nothing removed longer than {days} days ago.')
            return

        for c in doomed:
            removed = c.deleted_at.strftime('%Y-%m-%d')
            by = c.deleted_by.username if c.deleted_by else 'unknown'
            self.stdout.write(f'  {c.name}  (removed {removed} by {by})')

        if dry:
            self.stdout.write(self.style.WARNING(
                f'{len(doomed)} would be deleted permanently. Nothing was '
                f'touched — this was a dry run.'))
            return

        names = Community.purge_expired(days=days)
        self.stdout.write(self.style.SUCCESS(
            f'{len(names)} communit{"y" if len(names) == 1 else "ies"} deleted '
            f'permanently.'))
