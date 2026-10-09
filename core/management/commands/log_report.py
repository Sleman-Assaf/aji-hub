# core/management/commands/log_report.py
"""
Write a plain-text report of the audit log, and keep it.

    python manage.py log_report              # the last 7 days
    python manage.py log_report --days 30
    python manage.py log_report --all

Weekly, on the server:

    Linux cron, Mondays at 03:00
        0 3 * * 1 cd /srv/ajihub && .venv/bin/python manage.py log_report

    Windows Task Scheduler
        schtasks /create /tn "AJi Hub log report" /tr "C:\\path\\.venv\\Scripts\\python.exe
                 C:\\path\\manage.py log_report" /sc weekly /d MON /st 03:00

Files land in <backup folder>/reports/ and are named for the period they cover:

    ajihub-log-2026-W33-20260810-to-20260817.txt

Named that way on purpose. A report called "latest" tells you nothing about
what is in it, and one named only by the day it ran does not say what it
covers. The IT portal lists them and hands them back on request.

Text, not CSV or JSON: this is a record somebody reads, or attaches to a ticket,
or hands an auditor. Anything that wants the data itself can query the table.
"""
import os
from collections import Counter

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Write a dated text report of the audit log to the reports folder.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=7)
        parser.add_argument('--all', action='store_true', help='Every entry ever.')
        parser.add_argument('--quiet', action='store_true')

    def handle(self, *args, **opts):
        from datetime import timedelta
        from core.models import ITAuditLog, backup_dir

        now = timezone.now()
        if timezone.is_aware(now):
            now = timezone.localtime(now)

        rows = ITAuditLog.objects.select_related('actor').order_by('created_at')
        if opts['all']:
            since = None
            period = 'everything recorded'
            slug = f'all-to-{now:%Y%m%d}'
        else:
            since = now - timedelta(days=opts['days'])
            rows = rows.filter(created_at__gte=since)
            period = f'{since:%d %b %Y} to {now:%d %b %Y}'
            year, week, _ = now.isocalendar()
            slug = f'{year}-W{week:02d}-{since:%Y%m%d}-to-{now:%Y%m%d}'

        rows = list(rows)
        folder = os.path.join(backup_dir(), 'reports')
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f'ajihub-log-{slug}.txt')

        by_action = Counter(r.get_action_display() for r in rows)
        by_actor = Counter((r.actor.get_full_name() or r.actor.username)
                           if r.actor else 'system' for r in rows)

        lines = []
        w = lines.append
        w('AJi Hub — audit log')
        w('=' * 72)
        w(f'Period      {period}')
        w(f'Written     {now:%d %b %Y, %H:%M}')
        w(f'Entries     {len(rows)}')
        w('')

        if rows:
            w('Summary by kind')
            w('-' * 72)
            for label, n in by_action.most_common():
                w(f'  {n:>6}  {label}')
            w('')
            w('Summary by person')
            w('-' * 72)
            for who, n in by_actor.most_common(15):
                w(f'  {n:>6}  {who}')
            if len(by_actor) > 15:
                w(f'          … and {len(by_actor) - 15} more')
            w('')

        w('Entries, oldest first')
        w('-' * 72)
        if not rows:
            w('  Nothing was recorded in this period.')
        for r in rows:
            when = timezone.localtime(r.created_at) if timezone.is_aware(r.created_at) else r.created_at
            who = (r.actor.get_full_name() or r.actor.username) if r.actor else 'system'
            w(f'{when:%Y-%m-%d %H:%M}  {r.get_action_display():<18}  {who:<22}  {r.summary}')
            if r.ip:
                w(f'{"":18}from {r.ip}')
        w('')
        w('-' * 72)
        w('The log is written to and never edited. This report is a copy of it;')
        w('the entries themselves stay in the Hub.')

        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('\n'.join(lines) + '\n')

        if not opts['quiet']:
            self.stdout.write(f'{len(rows)} entries -> {path}')
        return path
