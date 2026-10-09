# taqdeer/management/commands/send_athar_reminder.py
"""The "nominations close soon" reminder for an open Athar quarter.

    manage.py send_athar_reminder

The Taqdeer settings page has offered this reminder, and a number of days to
send it before submissions close, since the module was written. Nothing sent it:
there was no command, no schedule, and no code that read either setting. HR
could switch a reminder on, choose five days, and nothing would ever arrive.
This is the missing half.

Run it hourly, like the message digest, for the same reason: "run it once at
09:00 on the right day" breaks the moment the machine is asleep at 09:00, and
nobody finds out for a quarter. It records the date it sent on the cycle itself,
so running it every hour — or twice by accident — reminds each manager once.

    schtasks /create /tn "AJi Hub Athar reminder" /sc hourly ^
      /tr "\"C:\\path\\to\\.venv\\Scripts\\python.exe\" \"C:\\path\\to\\manage.py\" send_athar_reminder"

Who it goes to is the point: only managers who have not yet used their whole
allowance. Reminding somebody who has already put forward their five names is
how a reminder becomes something people filter.

Flags:
    --dry-run   work out who would be reminded and print it, send nothing
    --force     ignore the day and the once-per-quarter record. Not the switch:
                if HR has turned reminders off, this sends nothing either.
"""
from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from core.models import NotificationEvent as Event
from core.notify import notify

from taqdeer.hr_views import eligible_submitters
from taqdeer.models import AwardCycle, Nomination, TaqdeerSettings


class Command(BaseCommand):
    help = 'Remind managers that Athar nominations are about to close.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--force', action='store_true')

    def handle(self, *args, **options):
        dry, force = options['dry_run'], options['force']
        s = TaqdeerSettings.get()

        # --force overrides the timing, never the switch. "Off" has to mean off
        # however the command is run, or the settings page is lying again.
        if not s.notify_reminder:
            self.stdout.write('Reminders are switched off in Taqdeer settings. '
                              'Nothing sent.')
            return

        cycle = AwardCycle.objects.filter(
            stage=AwardCycle.Stage.NOMINATING).order_by('-year', '-quarter').first()
        if cycle is None:
            self.stdout.write('No quarter is taking nominations. Nothing to do.')
            return
        if not cycle.closes_on:
            self.stdout.write(f'{cycle.label} has no closing date, so there is no '
                              f'day to count back from. Nothing sent.')
            return

        # `timezone.now().date()`, not `localdate()`: the Hub runs with USE_TZ
        # off, so now() is naive and localdate() refuses it. Same as everywhere
        # else in the codebase that needs today's date.
        days_left = (cycle.closes_on - timezone.now().date()).days
        due = s.reminder_days_before
        self.stdout.write(f'{cycle.label} closes {cycle.closes_on:%d %b %Y} — '
                          f'{days_left} day{"" if days_left == 1 else "s"} left, '
                          f'reminder is set for {due} day'
                          f'{"" if due == 1 else "s"} before.')

        if not force:
            if days_left != due:
                self.stdout.write('Not the day. Nothing sent.')
                return
            if cycle.reminder_sent_at:
                self.stdout.write(
                    f'Already reminded on '
                    f'{cycle.reminder_sent_at:%d %b %H:%M}. Nothing sent.')
                return

        # How many each manager has already put forward, so the reminder skips
        # anybody who is finished. One query rather than one per manager.
        used = {}
        for row in Nomination.objects.filter(cycle=cycle).values_list(
                'nominated_by_id', flat=True):
            used[row] = used.get(row, 0) + 1

        allowance = cycle.awards_per_manager
        when = f'Nominations close {cycle.closes_on:%d %B}.'
        link = reverse('taqdeer_submissions')

        reminded = skipped = 0
        for person in sorted(eligible_submitters(cycle), key=lambda u: u.pk):
            spent = used.get(person.pk, 0)
            left = allowance - spent
            if left <= 0:
                skipped += 1
                continue

            who = person.get_full_name() or person.username
            if dry:
                self.stdout.write(f'  would remind {who} — {left} of {allowance} '
                                  f'unused')
                reminded += 1
                continue

            notify(person, Event.APPROVAL,
                   title=f'{cycle.label} Athar nominations close soon',
                   body=f'{when} You have {left} of your {allowance} '
                        f'{"place" if left == 1 else "places"} left.',
                   link=link,
                   case='athar.closing_reminder',
                   values={
                       'name': who,
                       'period': cycle.label,
                       'days_left': (f'{days_left} '
                                     f'{"day" if days_left == 1 else "days"}'),
                       'closing': when,
                       'places_left': (f'{left} '
                                       f'{"place" if left == 1 else "places"}'),
                       'allowance': allowance,
                   })
            reminded += 1

        if not dry and reminded:
            cycle.reminder_sent_at = timezone.now()
            cycle.save(update_fields=['reminder_sent_at'])

        verb = 'would remind' if dry else 'reminded'
        self.stdout.write(self.style.SUCCESS(
            f'{verb} {reminded}, skipped {skipped} who had used their allowance'))
