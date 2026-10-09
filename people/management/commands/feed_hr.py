# people/management/commands/feed_hr.py
"""Run the HR feed by itself: fill Employee gaps from the Emp_HR table.

    python manage.py feed_hr --dry-run    show what would change
    python manage.py feed_hr              do it

The same pass that runs after every Excel import and at the tail of every AD
sync, callable alone — for after emptying the Employee table, or whenever the
directory looks thinner than HR's list. The rules live in people/hr_feed.py:
AD-provided values stand, HR fills what is empty, and names always follow the
HR spelling. Requires Emp_HR to be populated (manage.py import_hr_excel).
"""
from django.core.management.base import BaseCommand, CommandError

from people.hr_feed import apply_hr
from people.models import EmpHR


class Command(BaseCommand):
    help = 'Fill empty Employee fields from the imported HR data (Emp_HR).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing.')
        parser.add_argument('--show-unmatched', action='store_true',
                            help='List every HR row that found no Employee.')
        parser.add_argument('--create-missing', action='store_true',
                            help='Create a login-less Employee for every HR '
                                 'row with no match — site staff without AD '
                                 'accounts enter the directory this way.')

    def handle(self, *args, **opts):
        if not EmpHR.objects.exists():
            raise CommandError(
                'Emp_HR is empty — import the HR list first:\n'
                '    python manage.py import_hr_excel hr_data\\Emp-list.xlsx')
        report = apply_hr(dry_run=opts['dry_run'],
                          create_missing=opts['create_missing'])
        self.stdout.write(self.style.SUCCESS(report['message']))
        if report['unmatched'] and opts['show_unmatched']:
            self.stdout.write('Unmatched HR rows (no Employee yet — usually '
                              'people the AD sync has not created):')
            for name in report['unmatched_names']:
                self.stdout.write(f'  {name}')
            if report['unmatched'] > len(report['unmatched_names']):
                self.stdout.write(
                    f'  … and {report["unmatched"] - len(report["unmatched_names"])} more')
