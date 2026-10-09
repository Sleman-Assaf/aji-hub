# portal/management/commands/seed_hr_group.py
"""
python manage.py seed_hr_group
Creates the 'HR' permission group with permissions to manage People & Culture
and Onboarding content.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Create the HR permission group'

    def handle(self, *args, **options):
        from django.contrib.auth.models import Group, Permission
        from django.contrib.contenttypes.models import ContentType
        from portal.models import OnboardingHandbookItem, WellnessProgram, ChildcarePartner, GrowthCertification, CEOLetter, EmployeeHandbookFile
        from content.models import HRAnnouncement

        group, created = Group.objects.get_or_create(name='HR')
        if created:
            self.stdout.write('  Created group: HR')
        else:
            self.stdout.write('  Group already exists: HR')

        perms = []
        for model in [OnboardingHandbookItem, WellnessProgram, ChildcarePartner, GrowthCertification, HRAnnouncement, CEOLetter, EmployeeHandbookFile]:
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
                f'\nDone — HR group has {len(perms)} permission(s).\n'
                'Add users: Admin → Auth → Users → select user → Groups → HR'
            )
        )
