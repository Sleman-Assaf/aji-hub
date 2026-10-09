# Generated for messaging: give the call notes already in the database their kind.
"""Backfill `Message.kind` for the call lines written before the field existed.

Matching on the wording is exactly what `kind` was added to avoid — but it is
sound here and nowhere else. These rows were written by one function, in one
language, within a day of this migration, and the set of things it can have
written is the four strings below. Doing it once and storing the answer is what
lets the browser stop guessing.

Without this, every call already in a transcript stays an unmarked grey pill
while every call from now on gets its icon — the sort of inconsistency that
reads as a bug in the feature rather than as history.
"""
from django.db import migrations
from django.db.models import Q


def mark(apps, schema_editor):
    Message = apps.get_model('messaging', 'Message')
    system = Message.objects.filter(system=True, kind='')
    system.filter(Q(text__startswith='Missed voice call')
                  | Q(text__startswith='Voice call declined')).update(kind='call_missed')
    system.filter(text__startswith='Voice call ·').update(kind='call')


def unmark(apps, schema_editor):
    Message = apps.get_model('messaging', 'Message')
    Message.objects.filter(kind__in=['call', 'call_missed']).update(kind='')


class Migration(migrations.Migration):

    dependencies = [
        ('messaging', '0008_message_kind'),
    ]

    operations = [
        migrations.RunPython(mark, unmark),
    ]
