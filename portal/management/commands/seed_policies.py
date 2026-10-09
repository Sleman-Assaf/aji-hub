# portal/management/commands/seed_policies.py
"""
python manage.py seed_policies
Seeds PolicyCategory and sample Policy records.
"""
from django.core.management.base import BaseCommand


SEED = [
    {
        'category': ('Risk Assessment', 'Project & operational risk', 1),
        'policies': [
            ('Risk Management Framework',        'Identifies and mitigates project and operational risks across AJi entities.'),
            ('Project Risk Register Template',   'Standard template for logging and tracking risks on AJi projects.'),
            ('Business Continuity Plan',         'Procedures for maintaining operations during disruptions.'),
        ],
    },
    {
        'category': ('Cybersecurity Governance', 'IT security & data handling', 2),
        'policies': [
            ('Information Security Policy',      'Rules for protecting AJi data, systems and digital assets.'),
            ('Acceptable Use Policy',            'Guidelines for using AJi IT equipment and networks appropriately.'),
            ('Data Protection & GDPR Policy',   'Compliance requirements for handling personal data.'),
            ('Password & Access Management',     'Standards for creating and managing user credentials.'),
        ],
    },
    {
        'category': ('QHSE Policy', 'Quality, health, safety & environment', 3),
        'policies': [
            ('Quality Management Policy',        'AJi commitment to delivering projects to ISO 9001 standards.'),
            ('HSE Management Policy',            'Health, safety and environment obligations for all AJi staff.'),
            ('Incident Reporting Procedure',     'Steps to follow when reporting a safety incident or near-miss.'),
            ('Non-Conformance Procedure',        'How to raise and resolve quality non-conformances.'),
        ],
    },
    {
        'category': ('Environmental Aspects', 'ISO 14001 impacts', 4),
        'policies': [
            ('Environmental Policy',             'AJi commitment to minimising environmental impact of its operations.'),
            ('Environmental Aspects Register',   'Catalogue of significant environmental aspects and controls.'),
            ('Waste Management Procedure',       'Guidance on waste segregation, disposal and recycling on site.'),
        ],
    },
    {
        'category': ('HR Policies', 'Employment, leave & conduct', 5),
        'policies': [
            ('Code of Conduct',                  'Standards of professional behaviour expected of all AJi employees.'),
            ('Leave Policy',                     'Annual, sick and emergency leave entitlements and approval process.'),
            ('Performance Appraisal Policy',     'Framework for annual performance reviews and development plans.'),
            ('Disciplinary & Grievance Policy',  'Process for handling misconduct, complaints and appeals.'),
            ('Recruitment & Selection Policy',   'Standards for fair and consistent hiring across AJi Group.'),
        ],
    },
    {
        'category': ('Finance & Procurement', 'Budgets, approvals & purchasing', 6),
        'policies': [
            ('Procurement Policy',               'Rules for purchasing goods and services and selecting vendors.'),
            ('Expense Reimbursement Policy',     'Eligible expenses and the process for claiming reimbursement.'),
            ('Financial Delegation Authority',   'Approval thresholds for financial commitments by role.'),
        ],
    },
    {
        'category': ('IT & Digital', 'Systems, access & data management', 7),
        'policies': [
            ('IT Asset Management Policy',       'Responsibility for AJi-owned hardware, software and licenses.'),
            ('Remote Working Policy',            'Security and productivity expectations for remote staff.'),
            ('Software Licensing Policy',        'Rules for acquiring and using licensed software.'),
        ],
    },
    {
        'category': ('Health & Safety', 'Site safety & emergency procedures', 8),
        'policies': [
            ('Site Safety Plan',                 'Minimum safety requirements for AJi construction and supervision sites.'),
            ('Emergency Evacuation Procedure',   'Building evacuation procedures for AJi offices.'),
            ('PPE Policy',                       'Personal protective equipment requirements by site type.'),
        ],
    },
]


class Command(BaseCommand):
    help = 'Seed PolicyCategory and sample Policy records'

    def add_arguments(self, parser):
        parser.add_argument('--clear', action='store_true', help='Delete existing data first')

    def handle(self, *args, **options):
        from portal.models import PolicyCategory, Policy

        if options['clear']:
            Policy.objects.all().delete()
            PolicyCategory.objects.all().delete()
            self.stdout.write(self.style.WARNING('Cleared existing policies.'))

        for item in SEED:
            cat_name, cat_desc, cat_order = item['category']
            cat, created = PolicyCategory.objects.get_or_create(
                name=cat_name,
                defaults={'description': cat_desc, 'order': cat_order},
            )
            if created:
                self.stdout.write(f'  Created category: {cat_name}')

            for i, (title, desc) in enumerate(item['policies']):
                Policy.objects.get_or_create(
                    title=title,
                    defaults={'description': desc, 'category': cat, 'order': i},
                )

        self.stdout.write(self.style.SUCCESS(
            f'Done — {PolicyCategory.objects.count()} categories, {Policy.objects.count()} policies.'
        ))
        self.stdout.write('\nRun:  python manage.py seed_policies')
        self.stdout.write('Or:   python manage.py seed_policies --clear  (to reset)')
