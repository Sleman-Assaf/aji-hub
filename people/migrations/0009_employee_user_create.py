# people/migrations/0009_employee_user_create.py
"""
Data migration:
  1. Flush ALL non-superuser Django users
  2. Create one User per active Employee
     username = firstname.lastname.empcode  e.g. ahmad.khalil.8042
     email    = firstname.lastname@ajigroup.com
     password = unusable
"""
from django.db import migrations
from django.contrib.auth.models import User as RealUser
import re


def _slug(s):
    """Lowercase, keep only a-z0-9, strip everything else."""
    return re.sub(r'[^a-z0-9]', '', s.lower())


def _parts(full_name):
    parts = full_name.strip().split()
    first  = parts[0]          if parts          else 'user'
    last   = parts[-1]         if len(parts) > 1 else parts[0]
    return first, last


def create_users_for_employees(apps, schema_editor):
    Employee = apps.get_model('people', 'Employee')

    # 1. unlink then flush all non-superusers
    Employee.objects.update(user=None)
    RealUser.objects.filter(is_superuser=False).delete()

    # 2. create one user per active employee
    for emp in Employee.objects.filter(is_active=True):
        first, last = _parts(emp.full_name)
        code        = (emp.emp_code or '').strip()

        username = f"{_slug(first)}.{_slug(last)}.{_slug(code)}" if code \
                   else f"{_slug(first)}.{_slug(last)}"
        username = username[:150]

        # guarantee uniqueness (should be rare with emp_code appended)
        base, n = username, 1
        while RealUser.objects.filter(username=username).exists():
            username = f'{base}_{n}'
            n += 1

        email = f"{_slug(first)}.{_slug(last)}@ajigroup.com"

        user = RealUser(
            username   = username,
            email      = email,
            first_name = first[:30],
            last_name  = last[:150],
            is_active  = True,
        )
        user.set_unusable_password()
        user.save()

        emp.user_id = user.pk
        emp.save(update_fields=['user_id'])


def reverse_users(apps, schema_editor):
    Employee = apps.get_model('people', 'Employee')
    Employee.objects.update(user=None)
    RealUser.objects.filter(is_superuser=False).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('people', '0008_employee_office_drop_phoneextension'),
    ]

    operations = [
        migrations.RunPython(create_users_for_employees, reverse_users),
    ]
