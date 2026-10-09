# services/management/commands/seed_job_requisition_demo.py
from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand

from people.models import Department, Employee, Entity

DEMO_PASSWORD = 'pass123'

GROUPS = ['Hiring Managers', 'HR Managers', 'CEO']

# username -> (first_name, last_name, group_or_None, manager_username_or_None, emp_code)
# director1 gets no group: the director approval stage is resolved purely from
# Employee.manager (the requester's manager), not from group membership.
USERS = {
    'director1': ('Director', 'One', None, None, 'DEMO-DIR1'),
    'manager1':  ('Manager', 'One', 'Hiring Managers', 'director1', 'DEMO-MGR1'),
    'lina':      ('Lina', 'HR', 'HR Managers', None, 'DEMO-HR1'),
    'ceo':       ('CEO', 'AJi', 'CEO', None, 'DEMO-CEO1'),
}


class Command(BaseCommand):
    help = 'Seeds demo groups/users/employees for the Job Requisition approval workflow.'

    def handle(self, *args, **options):
        groups = {}
        for name in GROUPS:
            group, created = Group.objects.get_or_create(name=name)
            groups[name] = group
            self.stdout.write(f"{'Created' if created else 'Found'} group: {name}")

        entity, _ = Entity.objects.get_or_create(code='AJI-DEMO', defaults={'name': 'AJi Group'})
        department, _ = Department.objects.get_or_create(name='Job Requisition Demo', entity=entity)

        users = {}
        for username, (first, last, group_name, _manager, _emp_code) in USERS.items():
            user, created = User.objects.get_or_create(
                username=username,
                defaults={'first_name': first, 'last_name': last, 'email': f'{username}@ajigroup.com'},
            )
            if created:
                user.set_password(DEMO_PASSWORD)
                user.save()
                self.stdout.write(self.style.SUCCESS(f'Created user: {username} / {DEMO_PASSWORD}'))
            else:
                self.stdout.write(f'Found user: {username}')

            if group_name:
                user.groups.add(groups[group_name])
            users[username] = user

        # Employee records — required so JobRequisition can resolve the requester's
        # Director via Employee.manager. Create every employee first, then link managers.
        employees = {}
        for username, (first, last, _group, _manager, emp_code) in USERS.items():
            user = users[username]
            employee, created = Employee.objects.get_or_create(
                user=user,
                defaults={
                    'full_name': f'{first} {last}',
                    'email': user.email,
                    'emp_code': emp_code,
                    'entity': entity,
                    'department': department,
                    'role': 'Demo account for the Job Requisition workflow',
                    'is_active': True,
                },
            )
            if not created:
                # Backfill in case an earlier run (or a pre-existing Employee row)
                # left any of these fields empty.
                changed = False
                for field, value in (
                    ('entity', entity), ('department', department),
                    ('emp_code', emp_code), ('is_active', True),
                ):
                    if not getattr(employee, field):
                        setattr(employee, field, value)
                        changed = True
                if changed:
                    employee.save()
            employees[username] = employee
            self.stdout.write(f"{'Created' if created else 'Found'} employee record for: {username} (id={employee.pk})")

        for username, (_f, _l, _g, manager_username, _c) in USERS.items():
            if manager_username:
                employees[username].manager = employees[manager_username]
                employees[username].save(update_fields=['manager'])
                self.stdout.write(f'Linked {username} -> manager {manager_username}')

        self.stdout.write('')
        self.stdout.write('Verifying employee records (visible under People > Directory):')
        for username in USERS:
            emp = Employee.objects.get(user=users[username])
            self.stdout.write(
                f'  #{emp.pk} {emp.full_name} — active={emp.is_active}, '
                f'manager={emp.manager.full_name if emp.manager else "—"}'
            )

        self.stdout.write(self.style.SUCCESS(
            '\nDone. Demo accounts (all password "pass123"): manager1 (Hiring Managers, '
            "reports to director1), director1 (no group — approves as manager1's manager), "
            'lina (HR Managers), ceo (CEO).'
        ))
