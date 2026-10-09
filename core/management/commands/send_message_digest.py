# core/management/commands/send_message_digest.py
"""The end-of-day summary of unread chat messages, by email.

    manage.py send_message_digest

Run it hourly. It sends to each person only when the clock reaches *their*
chosen hour, and records the date it sent, so running it every hour — or twice by
accident, or again after a failure — still sends each person one email a day.

That matters because the obvious alternative, "run this once at 17:00", breaks
the moment the machine is asleep at 17:00, and nobody notices for a week.

Set it up on the Hub's server with Task Scheduler:

    schtasks /create /tn "AJi Hub message digest" /sc hourly ^
      /tr "\\"C:\\path\\to\\.venv\\Scripts\\python.exe\\" \\"C:\\path\\to\\manage.py\\" send_message_digest"

Flags:
    --dry-run       work out who would be emailed and print it, send nothing
    --force         ignore the hour and the once-a-day record
    --user USERNAME just this person, for testing
"""
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from core.mailer import transport_status
from core.models import NotificationPreference
from core.email_cases import resolve
from core.notify import absolute, render_email


class Command(BaseCommand):
    help = 'Email each person a summary of their unread chat messages.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--force', action='store_true')
        parser.add_argument('--user', default='')

    def handle(self, *args, **options):
        from messaging.models import ChatState, Conversation, Message

        now = timezone.now()
        today = now.date()
        dry, force = options['dry_run'], options['force']

        transport, explain = transport_status()
        self.stdout.write(explain)
        if transport is None and not dry:
            self.stdout.write(self.style.WARNING(
                'Nothing sent. Configure email on the Global Settings page.'))
            return

        prefs = (NotificationPreference.objects
                 .filter(digest_email=True)
                 .select_related('user')
                 .exclude(user__email=''))
        if options['user']:
            prefs = prefs.filter(user__username=options['user'])
        if not force:
            # Their hour has come today, and they have not had one today.
            prefs = prefs.filter(digest_hour__lte=now.hour).filter(
                Q(digest_last_sent__isnull=True) | ~Q(digest_last_sent=today))

        sent = skipped = failed = 0
        for pref in prefs:
            user = pref.user
            lines = _unread_by_sender(user, ChatState, Conversation, Message)
            if not lines:
                skipped += 1
                continue

            total = sum(n for _, n in lines)
            items = [(f'{name}', f'{n} unread message{"s" if n != 1 else ""}')
                     for name, n in lines]
            headline = (f'You have {total} unread message'
                        f'{"s" if total != 1 else ""}')
            shape = resolve('chat.daily_digest', {
                'name': user.get_full_name() or user.username,
                'unread_count': (f'{total} unread message'
                                 f'{"s" if total != 1 else ""}'),
                'conversation_count': (
                    f'{len(lines)} '
                    f'{"conversations" if len(lines) != 1 else "conversation"}'),
            })
            if shape is None or not shape.enabled:
                skipped += 1
                continue
            headline = shape.subject
            html = render_email(
                title=shape.subject,
                lines=shape.body_lines,
                items=items,
                link=absolute('/messaging/'),
                link_label=shape.link_label,
                footer=shape.footer,
            )

            who = user.get_full_name() or user.username
            if dry:
                self.stdout.write(f'  would email {who} <{user.email}>: '
                                  f'{headline} from ' + ', '.join(n for n, _ in lines))
                sent += 1
                continue

            from core.mailer import send
            ok, error = send(user.email, shape.subject, html,
                             high_importance=shape.high_importance)
            if ok:
                pref.digest_last_sent = today
                pref.save(update_fields=['digest_last_sent'])
                sent += 1
                self.stdout.write(self.style.SUCCESS(f'  sent to {who}'))
            else:
                failed += 1
                self.stdout.write(self.style.ERROR(f'  failed for {who}: {error}'))

        verb = 'would send' if dry else 'sent'
        self.stdout.write(f'{verb} {sent}, nothing to say for {skipped}, '
                          f'failed {failed}')


def _unread_by_sender(user, ChatState, Conversation, Message):
    """[(name, count)] — who is waiting on this person, most first.

    Named by sender rather than by conversation, because "3 unread messages from
    Ayah" is what somebody wants to read at the end of the day; "3 unread in
    conversation 41" is not. A group is named by the group.
    """
    states = {s.conversation_id: s for s in ChatState.objects.filter(user=user)}
    convs = list(user.conversations.prefetch_related('participants'))
    if not convs:
        return []

    tally = {}
    for conv in convs:
        state = states.get(conv.pk)
        qs = (Message.objects.filter(conversation=conv, system=False)
              .exclude(sender=user).exclude(hidden_for=user))
        if state and state.cleared_at:
            qs = qs.filter(sent_at__gt=state.cleared_at)
        if state and state.last_read_at:
            qs = qs.filter(sent_at__gt=state.last_read_at)
        n = qs.count()
        if not n:
            continue
        if conv.is_group:
            label = conv.name or 'a group'
        else:
            other = next((p for p in conv.participants.all() if p.pk != user.pk), None)
            label = (other.get_full_name() or other.username) if other else 'someone'
        tally[label] = tally.get(label, 0) + n

    return sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))
