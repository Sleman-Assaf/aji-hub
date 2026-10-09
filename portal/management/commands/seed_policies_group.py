# portal/management/commands/seed_policies_group.py
"""
python manage.py seed_policies_group
Creates the 'Policies' permission group with permissions on PolicyCategory and Policy.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Create the Policies permission group'

    def handle(self, *args, **options):
        from django.contrib.auth.models import Group, Permission
        from django.contrib.contenttypes.models import ContentType
        from portal.models import PolicyCategory, Policy

        group, created = Group.objects.get_or_create(name='Policies')
        if created:
            self.stdout.write('  Created group: Policies')
        else:
            self.stdout.write('  Group already exists: Policies')

        perms = []
        for model in [PolicyCategory, Policy]:
            ct = ContentType.objects.get_for_model(model)
            for action in ['add', 'change', 'delete', 'view']:
                codename = f'{action}_{model.__name__.lower()}'
                try:
                    perm = Permission.objects.get(content_type=ct, codename=codename)
                    perms.append(perm)
                except Permission.DoesNotExist:
                    self.stdout.write(
                        self.style.WARNING(f'  Permission not found: {codename} — run migrate first')
                    )

        group.permissions.set(perms)
        self.stdout.write(
            self.style.SUCCESS(
                f'\nDone — Policies group has {len(perms)} permission(s).\n'
                'Add users: Admin → Auth → Users → select user → Groups → Policies'
            )
        )
