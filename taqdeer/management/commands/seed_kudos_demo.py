"""A handful of Kudos, so the Recognition block shows its third kind.

There are nine already, and every one of them was hidden on purpose — by
nael.shunnar and hr, on 23 August, within a few minutes of each other. So this
does not unhide them: somebody's deliberate decision is not this command's to
reverse. It adds its own.

Everything it makes carries MARKER at the end of the message, which is how
`--remove` finds exactly its own work and nothing else. The marker is visible on
the post on purpose: somebody reading a sample Kudos should know inside a second
that a colleague did not actually write it.

    python manage.py seed_kudos_demo
    python manage.py seed_kudos_demo --remove
"""
import random
from datetime import timedelta

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.utils import timezone

from taqdeer.models import Praise

#: The last line of every message this command writes. Also the whole removal
#: story, since Praise has no slug to prefix.
MARKER = '[Sample Kudos — added to show the layout.]'

#: Subject, category, and the body. Categories match the ones already in use so
#: the filters on the Taqdeer page have something real to filter.
KUDOS = [
    ('Held the QHSE audit together', 'Quality',
     'Stayed late three evenings running to get the audit pack in order, and '
     'never once made it sound like a favour.'),
    ('Covered the site handover at short notice', 'Teamwork',
     'Stepped in on a day off when the handover moved, and the client never '
     'knew anything had changed.'),
    ('Rewrote the survey brief so it could be read', 'Quality',
     'Took eleven pages of notes and turned them into two that the site team '
     'actually use.'),
    ('Patient with a very long week', 'Teamwork',
     'Answered the same question from four different people without ever '
     'sounding like it was the fourth time.'),
    ('Spotted the drawing error before it was poured', 'Quality',
     'Caught a level discrepancy in the setting-out that would have cost a '
     'fortnight to put right.'),
    ('Mentoring the new graduates', 'Mentoring',
     'Sat with each of them in their first week, unprompted, and they have all '
     'said so separately.'),
]


class Command(BaseCommand):
    help = 'Add (or remove) sample Kudos so the Recognition block is populated.'

    def add_arguments(self, parser):
        parser.add_argument('--remove', action='store_true',
                            help='Delete the Kudos this command created.')
        parser.add_argument('--count', type=int, default=len(KUDOS))

    def handle(self, *args, **options):
        if options['remove']:
            return self._remove()

        people = list(User.objects.filter(is_active=True)
                      .exclude(username__in=('admin',))
                      .order_by('first_name', 'username'))
        if len(people) < 2:
            self.stderr.write('Need at least two active people to thank each other.')
            return

        rng = random.Random(20260824)
        now = timezone.now()
        made = 0

        for i, (subject, category, body) in enumerate(KUDOS[:options['count']]):
            if Praise.objects.filter(subject=subject,
                                     message__contains=MARKER).exists():
                self.stdout.write(f'  already there: {subject}')
                continue

            # A different pair each time, and never somebody thanking themselves.
            giver = people[i % len(people)]
            others = [p for p in people if p.pk != giver.pk]
            recipient = others[rng.randrange(len(others))]

            praise = Praise.objects.create(
                giver=giver, recipient=recipient,
                subject=subject, category=category,
                message=f'{body}\n\n{MARKER}',
                status=Praise.Status.APPROVED if hasattr(Praise, 'Status') else 'approved',
            )
            # Spread back over the last fortnight so the block's newest-first
            # ordering has something to order, and so they interleave with the
            # certification and the award rather than arriving in one clump.
            Praise.objects.filter(pk=praise.pk).update(
                created_at=now - timedelta(days=i, hours=rng.randrange(1, 20)))
            made += 1
            self.stdout.write(
                f'  {recipient.get_full_name() or recipient.username}'
                f'  ←  {giver.get_full_name() or giver.username}   {subject[:38]}')

        self.stdout.write(self.style.SUCCESS(f'{made} sample Kudos added.'))
        self.stdout.write('Remove them again with: manage.py seed_kudos_demo --remove')
        hidden = Praise.objects.filter(hidden_at__isnull=False).count()
        if hidden:
            self.stdout.write(
                f'({hidden} older Kudos remain hidden — left exactly as they were.)')

    def _remove(self):
        qs = Praise.objects.filter(message__contains=MARKER)
        n = qs.count()
        if not n:
            self.stdout.write('Nothing to remove.')
            return
        qs.delete()
        self.stdout.write(self.style.SUCCESS(f'{n} sample Kudos removed.'))
