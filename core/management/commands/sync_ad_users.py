# core/management/commands/sync_ad_users.py
"""Import everyone from Active Directory and keep the Hub in step with it.

    python manage.py sync_ad_users                       one pass
    python manage.py sync_ad_users --dry-run             show, change nothing
    python manage.py sync_ad_users --deactivate-missing  also disable leavers
    python manage.py sync_ad_users --loop 60             keep syncing, hourly

"Keep syncing" two ways: --loop runs forever in this process (register it as
a service beside Waitress), or schedule single passes with Task Scheduler:

    schtasks /Create /TN "AJi AD Sync" /SC HOURLY ^
        /TR "C:\\...\\.venv\\Scripts\\python.exe C:\\...\\manage.py sync_ad_users --deactivate-missing"

Uses the LDAP settings from Global Settings (/settings/), which must include
the service (bind) account — enumerating the directory is a read that
anonymous binds are refused for. The rules that keep a sync from ever locking
you out are documented in core/ldap_sync.py: local-password accounts and
superusers are never deactivated, and nothing is ever deleted.
"""
import time

from django.core.management.base import BaseCommand, CommandError

from core import ldap_sync


class Command(BaseCommand):
    help = 'Import all users from Active Directory and sync names, emails and the active flag.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing.')
        parser.add_argument('--deactivate-missing', action='store_true',
                            help='Deactivate AD-sourced accounts no longer in '
                                 'the directory (never local or superusers).')
        parser.add_argument('--loop', type=int, metavar='MINUTES', default=0,
                            help='Keep running, syncing every N minutes.')

    def handle(self, *args, **opts):
        while True:
            report = ldap_sync.sync_users(
                dry_run=opts['dry_run'],
                deactivate_missing=opts['deactivate_missing'],
                ran_by='sync_ad_users command')
            if not report.get('ok'):
                # In loop mode a DC being briefly unreachable is a retry, not
                # a crash; run-once mode should fail loudly for the operator.
                if opts['loop']:
                    self.stderr.write(self.style.WARNING(report['message']))
                else:
                    raise CommandError(report['message'])
            else:
                self.stdout.write(self.style.SUCCESS(report['message']))
                if report['names']['created']:
                    self.stdout.write('  new: ' +
                                      ', '.join(report['names']['created']))
                if report['names']['deactivated']:
                    self.stdout.write('  deactivated: ' +
                                      ', '.join(report['names']['deactivated']))
            if not opts['loop']:
                break
            time.sleep(max(1, opts['loop']) * 60)
