# core/migrations/0005_carry_mention_pref.py
"""Carry the single old mention switch into the new per-channel one.

`notify_on_mention` was the only notification setting the Hub had. Dropping it
and relying on the new field's default would switch mentions back on for anybody
who had deliberately switched them off — a schema change quietly overruling
somebody's decision. So it is read first, then removed.
"""
from django.db import migrations


def carry(apps, schema_editor):
    Pref = apps.get_model('core', 'NotificationPreference')
    # Only the people who turned it off need writing; the rest already match.
    n = Pref.objects.filter(notify_on_mention=False).update(mention_in_app=False,
                                                            mention_browser=False,
                                                            mention_email=False)
    if n:
        print(f'  carried "no mention notifications" for {n} user(s)')


def uncarry(apps, schema_editor):
    Pref = apps.get_model('core', 'NotificationPreference')
    Pref.objects.filter(mention_in_app=False).update(notify_on_mention=False)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_notificationpreference_approval_browser_and_more'),
    ]

    operations = [
        migrations.RunPython(carry, uncarry),
    ]
