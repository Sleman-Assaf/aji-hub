# services/management/commands/seed_service_categories.py
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from services.models import Service, ServiceCategory

CATEGORIES = [
    # name, category_type, icon, order
    ('Approvals', ServiceCategory.CategoryType.APPROVALS, 'approvals', 0),
    ('HR', ServiceCategory.CategoryType.SERVICES, 'hr', 1),
    ('Finance & Expenses', ServiceCategory.CategoryType.SERVICES, 'finance', 2),
    ('IT & Tech Support', ServiceCategory.CategoryType.SERVICES, 'it', 3),
    ('Workplace & Facilities', ServiceCategory.CategoryType.SERVICES, 'facilities', 4),
]

# (category name, service name, description, group name, service_key, order)
SERVICES = [
    ('HR', 'Job Requisition', 'Request approval to open a new position',
     'Hiring Managers', 'job_requisition', 0),
]


class Command(BaseCommand):
    help = 'Seeds the My AJi main categories (Approvals, HR, Finance & Expenses, IT & Tech Support, Workplace & Facilities) and the Job Requisition service under HR.'

    def handle(self, *args, **options):
        categories = {}
        for name, category_type, icon, order in CATEGORIES:
            cat, created = ServiceCategory.objects.get_or_create(
                name=name,
                defaults={'category_type': category_type, 'icon': icon, 'order': order},
            )
            categories[name] = cat
            self.stdout.write(f"{'Created' if created else 'Found'} category: {name}")

        for cat_name, svc_name, description, group_name, service_key, order in SERVICES:
            group = None
            if group_name:
                group, _ = Group.objects.get_or_create(name=group_name)
            svc, created = Service.objects.get_or_create(
                service_key=service_key,
                defaults={
                    'category': categories[cat_name],
                    'name': svc_name,
                    'description': description,
                    'group': group,
                    'order': order,
                },
            )
            self.stdout.write(f"{'Created' if created else 'Found'} service: {svc_name} (under {cat_name})")

        self.stdout.write(self.style.SUCCESS(
            '\nDone. My AJi categories seeded: Approvals, HR, Finance & Expenses, '
            'IT & Tech Support, Workplace & Facilities. Job Requisition seeded under HR '
            '(visible to the "Hiring Managers" group).'
        ))
