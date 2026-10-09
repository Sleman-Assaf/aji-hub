"""Rename Ahsant to Kudos in text that was already written to the database.

The label lives in templates and in notify() calls, so renaming it in code
covers everything said from now on. It does not cover what was already said:
notifications carry the sentence they were created with, so a bell row from last
week still read "Nael sent you an Ahsant" against a product with no Ahsant in it.

Article-aware for the same reason the code change was: Kudos takes no article, so
"an Ahsant" becomes "Kudos" rather than "a Kudos".

Reversible, because a rename is a decision and decisions get revisited.
"""
import re

from django.db import migrations

PAIRS = [(r'\ban Ahsant\b', 'Kudos'), (r'\bAn Ahsant\b', 'Kudos'),
         (r'Ahsant', 'Kudos'), (r'ahsant', 'kudos')]
BACK = [(r'\bKudos\b', 'Ahsant'), (r'kudos', 'ahsant')]


def _sweep(apps, pairs):
    Notification = apps.get_model('core', 'Notification')
    hit = pairs[0][0].split('\b')[1] if '\b' in pairs[0][0] else pairs[0][0]
    needle = 'Ahsant' if 'Ahsant' in str(pairs) else 'Kudos'
    touched = 0
    rows = Notification.objects.filter(title__icontains=needle) | \
        Notification.objects.filter(body__icontains=needle)
    for row in rows.distinct():
        title, body = row.title, row.body
        for pat, to in pairs:
            title = re.sub(pat, to, title)
            body = re.sub(pat, to, body or '')
        if (title, body) != (row.title, row.body):
            row.title, row.body = title, body
            row.save(update_fields=['title', 'body'])
            touched += 1
    print(f'  rewrote {touched} notification(s)')


def forwards(apps, schema_editor):
    _sweep(apps, PAIRS)


def backwards(apps, schema_editor):
    _sweep(apps, BACK)


class Migration(migrations.Migration):

    dependencies = [
        ('taqdeer', '0012_comment_edited_at'),
        ('core', '0001_initial'),
    ]

    operations = [migrations.RunPython(forwards, backwards)]
