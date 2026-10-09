# messaging/migrations/0004_seed_read_state.py
"""Carry the old per-message `read` flag across to per-person timestamps.

`Message.read` was one boolean shared by everyone, which cannot answer "has
everybody in this group seen it?". `ChatState.last_read_at` can, and gives the
same answer for a direct chat.

Throwing the old flag away would mark every conversation unread the next time
anybody opened the Hub, so this reads it first: for each person, the newest
message they had already read becomes their read mark. Anyone with nothing read
keeps a null mark, which correctly means "everything is unread".
"""
from django.db import migrations


def seed(apps, schema_editor):
    Conversation = apps.get_model('messaging', 'Conversation')
    ChatState = apps.get_model('messaging', 'ChatState')

    rows = []
    for conv in Conversation.objects.prefetch_related('participants', 'messages'):
        messages = list(conv.messages.all())
        for user in conv.participants.all():
            # A message I sent is one I have obviously seen, so only the ones
            # sent *to* me decide where my read mark sits.
            seen = [m.sent_at for m in messages
                    if m.sender_id != user.pk and m.read]
            rows.append(ChatState(conversation=conv, user=user,
                                  last_read_at=max(seen) if seen else None))
    if rows:
        ChatState.objects.bulk_create(rows, ignore_conflicts=True)


def unseed(apps, schema_editor):
    """Reverse by restoring the flag from the timestamps, not by dropping it."""
    Conversation = apps.get_model('messaging', 'Conversation')
    Message = apps.get_model('messaging', 'Message')
    ChatState = apps.get_model('messaging', 'ChatState')

    for conv in Conversation.objects.all():
        for state in ChatState.objects.filter(conversation=conv):
            if state.last_read_at:
                (Message.objects
                 .filter(conversation=conv, sent_at__lte=state.last_read_at)
                 .exclude(sender_id=state.user_id)
                 .update(read=True))
    ChatState.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('messaging', '0003_conversation_created_by_conversation_kind_and_more'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
