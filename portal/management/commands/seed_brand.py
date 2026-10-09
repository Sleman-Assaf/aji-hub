# portal/management/commands/seed_brand.py
"""
python manage.py seed_brand
Seeds BrandSection, BrandTemplate and placeholder BrandTemplateFile records.
"""
from django.core.management.base import BaseCommand


SEED = [
    {
        'section': ('Templates', 'templates', 1),
        'templates': [
            {
                'name': 'AJi Logo',
                'description': 'SVG · PNG',
                'order': 1,
                'files': [
                    'AJi-Logo-Primary.svg',
                    'AJi-Logo-White.svg',
                    'AJi-Logo-Black.svg',
                    'AJi-Logo-Yellow.png',
                    'AJi-Icon.svg',
                ],
            },
            {
                'name': 'Presentation',
                'description': 'PPTX · 16:9 widescreen',
                'order': 2,
                'files': [
                    'AJi-Presentation-Template.pptx',
                    'AJi-Presentation-Template-Dark.pptx',
                ],
            },
            {
                'name': 'Report Template',
                'description': 'DOCX · Project report format',
                'order': 3,
                'files': [
                    'AJi-Report-Template.docx',
                ],
            },
            {
                'name': 'Proposal Template',
                'description': 'DOCX · Technical & commercial',
                'order': 4,
                'files': [
                    'AJi-Proposal-Template.docx',
                    'AJi-Proposal-Cover-Sheet.docx',
                ],
            },
            {
                'name': 'New Project Sheet',
                'description': 'XLSX · Project kickoff data',
                'order': 5,
                'files': [
                    'AJi-New-Project-Sheet.xlsx',
                ],
            },
            {
                'name': 'Invoice',
                'description': 'XLSX · Standard AJi invoice',
                'order': 6,
                'files': [
                    'AJi-Invoice-Template.xlsx',
                ],
            },
            {
                'name': 'Email Signature',
                'description': 'HTML · Outlook compatible',
                'order': 7,
                'files': [
                    'AJi-Email-Signature.html',
                    'AJi-Email-Signature-Instructions.pdf',
                ],
            },
            {
                'name': 'LinkedIn Cover',
                'description': 'PNG · 1584 × 396 px',
                'order': 8,
                'files': [
                    'AJi-LinkedIn-Cover.png',
                    'AJi-LinkedIn-Cover-EN.png',
                    'AJi-LinkedIn-Cover-AR.png',
                ],
            },
        ],
    },
    {
        'section': ('Brand Assets', 'brand_assets', 2),
        'templates': [
            {
                'name': 'Brand Guidelines',
                'description': 'PDF · Visual identity guide',
                'order': 1,
                'files': [
                    'AJi-Brand-Guidelines-2025.pdf',
                ],
            },
            {
                'name': 'Company Naming',
                'description': 'PDF · Entity abbreviation guide',
                'order': 2,
                'files': [
                    'AJi-Company-Naming-Guide.pdf',
                ],
            },
            {
                'name': 'Rebranding Letter',
                'description': 'PDF · Official announcement',
                'order': 3,
                'files': [
                    'AJi-Rebranding-Letter-EN.pdf',
                    'AJi-Rebranding-Letter-AR.pdf',
                ],
            },
        ],
    },
]


class Command(BaseCommand):
    help = 'Seed BrandSection, BrandTemplate and BrandTemplateFile records'

    def add_arguments(self, parser):
        parser.add_argument('--clear', action='store_true', help='Delete existing data first')

    def handle(self, *args, **options):
        from portal.models import BrandSection, BrandTemplate, BrandTemplateFile

        if options['clear']:
            BrandTemplateFile.objects.all().delete()
            BrandTemplate.objects.all().delete()
            BrandSection.objects.all().delete()
            self.stdout.write(self.style.WARNING('Cleared existing brand data.'))

        for item in SEED:
            sec_name, sec_type, sec_order = item['section']
            section, created = BrandSection.objects.get_or_create(
                name=sec_name,
                defaults={'section_type': sec_type, 'order': sec_order},
            )
            if created:
                self.stdout.write(f'  Created section: {sec_name}')

            for t in item['templates']:
                tmpl, _ = BrandTemplate.objects.get_or_create(
                    name=t['name'],
                    section=section,
                    defaults={
                        'description': t['description'],
                        'order':       t['order'],
                    },
                )

                for fname in t['files']:
                    BrandTemplateFile.objects.get_or_create(
                        template=tmpl,
                        label=fname,
                        defaults={'version': 'v1.0'},
                    )

                self.stdout.write(
                    f'    {tmpl.name}: {tmpl.files.count()} file(s)'
                )

        total_templates = BrandTemplate.objects.count()
        total_files     = BrandTemplateFile.objects.count()
        self.stdout.write(self.style.SUCCESS(
            f'  Templates — {BrandSection.objects.count()} sections, '
            f'{total_templates} templates, {total_files} files.'
        ))

        # ── Colours ──────────────────────────────────────────────────────────
        from portal.models import BrandColour
        COLOURS = [
            ('Yellow', '#FCD700', 'Pantone 012 C',  False, 1),
            ('Black',  '#000000', 'C0 M0 Y0 K100', False, 2),
            ('White',  '#FFFFFF', 'C0 M0 Y0 K0',   True,  3),
        ]
        for name, hex_, ref, light, order in COLOURS:
            BrandColour.objects.get_or_create(
                name=name,
                defaults={'hex': hex_, 'ref': ref, 'light': light, 'order': order},
            )
        self.stdout.write(f'  Colours — {BrandColour.objects.count()} seeded')

        # ── Booth albums ──────────────────────────────────────────────────────
        from portal.models import BoothAlbum
        BOOTH = [
            ("AJi Football League",          "Men's League · Jun 2024",        "sport",        6,  1),
            ("H1 Town Hall",                 "12 June · HQ Auditorium",        "company-wide", 8,  2),
            ("Pink October Checkups",        "Breast Cancer Awareness · 2025", "wellness",     5,  3),
            ("AJi Walk",                     "2025",                           "wellness",     7,  4),
            ("Eid Al-Adha Office Party",     "Jul 2025",                       "celebration",  9,  5),
            ("AJi Steps Challenge — Awards", "Oct 2025",                       "wellness",     4,  6),
        ]
        for name, period, tag, count, order in BOOTH:
            BoothAlbum.objects.get_or_create(
                name=name,
                defaults={'period': period, 'tag': tag, 'photo_count': count, 'order': order},
            )
        self.stdout.write(f'  Booth — {BoothAlbum.objects.count()} albums seeded')

        self.stdout.write(self.style.SUCCESS('\nAll brand seed data done.'))
        self.stdout.write('Run:  python manage.py seed_brand')
        self.stdout.write('Or:   python manage.py seed_brand --clear  (to reset)')
