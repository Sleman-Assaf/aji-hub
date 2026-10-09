# services/management/commands/fleet_reminders.py
"""Email the approvers about insurance, licences and services coming due.

    manage.py fleet_reminders

Run it once a day. Each car is checked against four deadlines — insurance,
licence, the next service date and the next service reading — and anything
inside thirty days produces one email per division, listing every car of that
division that needs attention, to the people who approve that division's cars.

Nothing is ever sent twice. A reminder is recorded against the car, the thing,
the deadline and the stage, so running this hourly, or twice by accident, or
again after a failure, still sends one email per stage. A renewed policy has a
new expiry date, which is a new deadline, and so is reminded about again — as
it should be.

Set it up on the Hub's server with Task Scheduler:

    schtasks /create /tn "AJi Hub fleet reminders" /sc daily /st 07:30 ^
      /tr "\\"C:\\path\\to\\.venv\\Scripts\\python.exe\\" \\"C:\\path\\to\\manage.py\\" fleet_reminders"

Flags:
    --dry-run    work out what would be sent and print it, send nothing
    --force      send even if the same reminder has already gone out
    --entity BE  only this division
"""
from django.core.management.base import BaseCommand

from core.mailer import transport_status
from people.models import Entity
from services import fleet
from services.models import FleetReminder, Vehicle


class Command(BaseCommand):
    help = 'Email approvers about insurance, licence and service dates coming due.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--force', action='store_true')
        parser.add_argument('--entity', default='')

    def handle(self, *args, **opts):
        dry, force = opts['dry_run'], opts['force']

        transport, warning = transport_status()
        if warning and not dry:
            self.stdout.write(self.style.WARNING('  ' + warning))
        if not transport and not dry:
            self.stdout.write(self.style.ERROR(
                '  No email transport, so nothing can be sent. Stopping.'))
            return

        cars = (Vehicle.objects.exclude(status=Vehicle.Status.RETIRED)
                .select_related('entity').order_by('entity__code', 'make'))
        if opts['entity']:
            cars = cars.filter(entity__code__iexact=opts['entity'])

        # Gather first, per division, so five cars of one division are one
        # email and not five.
        pending = {}
        for car in cars:
            for item in fleet.due_items(car):
                already = FleetReminder.objects.filter(
                    vehicle=car, kind=item['kind'], stage=item['stage'],
                    due_on=item['due_on'], due_km=item['due_km']).exists()
                if already and not force:
                    continue
                pending.setdefault(car.entity_id, []).append(item)

        if not pending:
            self.stdout.write('  Nothing is due that has not already been sent.')
            return

        sent_rows = 0
        for entity_id, items in pending.items():
            entity = Entity.objects.get(pk=entity_id)
            self.stdout.write(self.style.MIGRATE_HEADING(
                '\n  %s - %d thing%s' % (entity.code, len(items),
                                         '' if len(items) == 1 else 's')))
            for i in items:
                self.stdout.write('    %-28s %s' % (
                    (i['vehicle'].plate or i['vehicle'].make)[:28], i['text']))

            out = fleet.notify_due(entity, items, dry_run=dry)
            who = ', '.join(out['to']) or '(nobody with email)'
            if dry:
                self.stdout.write('    would email: %s' % who)
                continue

            self.stdout.write(self.style.SUCCESS(
                '    emailed %d, bell %d: %s'
                % (out['email'], out['in_app'], who)))
            for i in items:
                FleetReminder.objects.update_or_create(
                    vehicle=i['vehicle'], kind=i['kind'], stage=i['stage'],
                    due_on=i['due_on'], due_km=i['due_km'],
                    defaults={'recipients': who[:400]})
                sent_rows += 1

        if not dry:
            self.stdout.write('\n  %d reminder%s recorded.'
                              % (sent_rows, '' if sent_rows == 1 else 's'))
