# core/management/commands/seed_data.py
"""
Seed AJi Hub with initial data extracted from the HTML prototype.
Run: python manage.py seed_data
"""
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils.text import slugify


class Command(BaseCommand):
    help = 'Seed AJi Hub with initial data from the HTML prototype'

    def handle(self, *args, **options):
        self._seed_entities()
        self._seed_skill_categories()
        self._seed_projects()
        self._seed_onboarding()
        self._seed_communities()
        self._seed_policies()
        self._seed_events()
        self._seed_news()
        self._seed_superuser()
        self.stdout.write(self.style.SUCCESS('✅ Seed data loaded successfully!'))

    # ── ENTITIES ──────────────────────────────────────────────────────────────

    def _seed_entities(self):
        from people.models import Entity
        entities = [
            ('AJi', 'AJi Group (HQ)', 'AJi HQ'),
            ('AJiBE', 'AJ Built Environment', 'AJiBE'),
            ('AJiWE', 'AJi Water & Environment', 'AJiWE'),
            ('AJiSaudi', 'AJi Saudi', 'AJi Saudi'),
            ('HDP', 'HDP (UAE)', 'HDP'),
            ('Oman', 'AJi Oman', 'AJi Oman'),
            ('Limana', 'Limana', 'Limana'),
            ('Raseel', 'Raseel', 'Raseel'),
        ]
        for code, name, short in entities:
            Entity.objects.get_or_create(code=code, defaults={'name': name, 'short_name': short})
        self.stdout.write('  Entities ✓')

    # ── SKILL CATEGORIES ──────────────────────────────────────────────────────

    def _seed_skill_categories(self):
        from people.models import SkillCategory, Skill
        cats = {
            'Software': ['Revit', 'AutoCAD', 'Civil 3D', 'Navisworks', 'ArchiCAD', 'SketchUp', 'Rhino', 'STAAD.Pro', 'ETABS', 'SAP2000', 'Primavera P6', 'MS Project', 'BIM 360', 'InfraWorks', 'Bentley OpenRoads', 'WaterGEMS', 'Adobe Creative Suite'],
            'Disciplines': ['Architecture', 'Structural', 'MEP', 'Electrical', 'Mechanical', 'Geotechnical', 'Water & Environment', 'Roads & Transport', 'Urban Planning', 'Landscape', 'Surveying', 'Quantity Surveying'],
            'Sectors': ['Healthcare', 'Education', 'Aviation', 'Hospitality', 'Commercial', 'Residential', 'Industrial', 'Infrastructure', 'Wastewater', 'Mixed-Use'],
            'Standards & Methods': ['ISO 19650 / BIM', 'FIDIC', 'LEED', 'Saudi Building Code', 'Value Engineering', 'QA/QC', 'HSE', 'Clash Detection', 'BEP / CDE'],
        }
        for i, (cat_name, skills) in enumerate(cats.items()):
            cat, _ = SkillCategory.objects.get_or_create(name=cat_name, defaults={'order': i})
            for skill_name in skills:
                Skill.objects.get_or_create(name=skill_name, category=cat)
        self.stdout.write('  Skills ✓')

    # ── PROJECTS ──────────────────────────────────────────────────────────────

    def _seed_projects(self):
        from content.models import Project
        projects = [
            ('MOD Water Treatment Plant', 'Zubair Oil Field · Iraq', 'AJiWE', 'Water & Environment', 2026, 'On site', 'ajiwe-zubair'),
            ('KAFD Mixed-Use Tower', 'Riyadh · Saudi Arabia', 'AJiSaudi', 'Architecture', 2025, 'Design', 'kafd-tower'),
            ('Muscat Coastal Highway', 'Muscat · Oman', 'Oman', 'Roads & Transport', 2026, 'Tender', 'muscat-highway'),
            ('Ajman Specialist Hospital', 'Ajman · UAE', 'HDP', 'Healthcare', 2025, 'On site', 'ajman-hospital'),
            ('Aqaba Wastewater Network', 'Aqaba · Jordan', 'AJiWE', 'Water & Environment', 2026, 'On site', 'aqaba-water'),
            ('Riyadh Metro Depot Fit-out', 'Riyadh · Saudi Arabia', 'AJiSaudi', 'Infrastructure', 2024, 'Completed', 'riyadh-metro'),
            ('Abdoun Mixed-Use Mall', 'Amman · Jordan', 'AJiBE', 'Architecture', 2026, 'Design', 'abdoun-mall'),
            ('Tabuk University Expansion', 'Tabuk · Saudi Arabia', 'AJiSaudi', 'Urban', 2025, 'On site', 'tabuk-campus'),
            ('Dead Sea Resort Rehabilitation', 'Dead Sea · Jordan', 'AJiBE', 'Architecture', 2025, 'On site', 'deadsea-resort'),
            ('Abu Dhabi Biomedical Facility', 'Abu Dhabi · UAE', 'HDP', 'Healthcare', 2026, 'Design', 'ad-biomed'),
            ('Jerash Water Networks', 'Jerash · Jordan', 'AJiWE', 'Water & Environment', 2024, 'Completed', 'jerash-water'),
            ('Ayla Island Apartments', 'Aqaba · Jordan', 'Raseel', 'Architecture', 2026, 'On site', 'ayla-apartments'),
        ]
        for title, loc, div, ptype, year, status, seed in projects:
            Project.objects.get_or_create(
                title=title,
                defaults={'location': loc, 'division': div, 'type': ptype,
                          'year': year, 'status': status, 'image_seed': seed}
            )
        self.stdout.write('  Projects ✓')

    # ── ONBOARDING ────────────────────────────────────────────────────────────

    def _seed_onboarding(self):
        from portal.models import OnboardingSection, OnboardingItem
        sections = [
            ('conduct', 'Code of Conduct', 0, [
                ('Respectful Workplace', 'Treat colleagues, clients and partners with respect and dignity, avoid any harassment or discrimination, and contribute to a positive, collaborative culture.'),
                ('Equal Opportunity', 'AJi is an equal-opportunity employer — decisions are based on merit, qualifications and business needs.'),
                ('Conflict of Interest', 'Avoid activities or interests that conflict with AJi. Notify your manager immediately; outside work needs direct-manager and HR approval.'),
                ('Confidentiality', 'Confidential information must not be used or disclosed during or after your employment.'),
            ]),
            ('att', 'Attendance', 1, [
                ('Working hours', '08:00 – 17:00. Overtime is unpaid unless pre-approved by your direct manager via a request on Mena-me.'),
                ('Punching In / Out', 'Record every entry and exit via the clock at the AJi building reception.'),
                ('Overtime', 'Subject to workload and prior approval from the department head.'),
                ('Lunch break', '15 minutes, between 13:30 and 14:00.'),
                ('Ramadan timings', 'At the discretion of management.'),
            ]),
            ('leave', 'Time Off', 2, [
                ('Annual vacation', 'As per your employment contract — subject to prior online approval.'),
                ('Sick leave', 'One day can be reported by phone; more than one day needs a medical report. Apply via Mena-me.'),
                ('Paternity leave', '3 days, per Jordanian company law.'),
                ('Grief leave', '3 days for an immediate family member (parent, child, spouse, sibling).'),
                ('Marriage leave', '3 days, after one year with the company.'),
                ('Maternity leave', '70 days, per Jordanian company law.'),
                ('Haj leave', '10 working days after 5 years — once during employment.'),
            ]),
            ('prof', 'Workplace', 3, [
                ('Health & Safety', 'Report hazards, near-misses and accidents, look out for colleagues, and record incidents in the accident register.'),
                ('Dress Code', 'Formal and smart-casual — clean and neat, appropriate to your role, clients and culture.'),
                ('Car Parking', 'Up to 40 spaces to the right of the entrance — park appropriately without blocking neighbours\' garages.'),
                ('Computer Security', 'Never share your password, shut down your PC when leaving, and protect it from damage and spillage.'),
                ('Smoking', 'Non-smoking building; smoking only in outdoor designated areas, kept clean.'),
                ('Prayer Rooms', 'Prayer rooms and ablution areas are provided; please use ablution areas rather than public toilets.'),
            ]),
            ('comp', 'Compensation', 4, [
                ('Salary', 'Confidential and paid into a company-approved bank account, with a full breakdown on your online payslip.'),
                ('Annual review', 'Salary is reviewed annually, subject to your performance, manpower availability and market conditions.'),
                ('Medical insurance', 'Class A via MedNet after probation — 15% employee contribution on outpatient; inpatient fully covered.'),
                ('Life insurance', 'Company-provided life cover for all employees.'),
                ('Social Security', 'Enrolled per Jordanian law, with employer and employee contributions.'),
            ]),
            ('perf', 'Performance', 5, [
                ('Probationary review', '90 days after joining; confirmation follows a satisfactory evaluation.'),
                ('Annual performance review', 'Conducted by your direct manager to review performance, set goals and plan training each year.'),
                ('Complaint procedure', 'File a complaint against a colleague or manager with HR, who will take the necessary steps to resolve it.'),
            ]),
            ('exit', 'Leaving AJi', 6, [
                ('During probation', 'Either party may end employment at any time during probation, without notice or reason.'),
                ('Resignation', 'Resign at any time with a minimum of one month\'s notice.'),
                ('Exit interview', 'A confidential interview in your last week to help us improve the organisation.'),
                ('References', 'You\'re entitled to a record-of-service letter; reference letters are signed by your department head.'),
            ]),
        ]
        for tab_key, title, order, items in sections:
            section, _ = OnboardingSection.objects.get_or_create(
                tab_key=tab_key, defaults={'title': title, 'order': order}
            )
            for i, (label, content) in enumerate(items):
                OnboardingItem.objects.get_or_create(
                    section=section, label=label, defaults={'content': content, 'order': i}
                )
        self.stdout.write('  Onboarding ✓')

    # ── COMMUNITIES ───────────────────────────────────────────────────────────

    def _seed_communities(self):
        from exchange.models import Community
        communities = [
            ('BIM & Digital Twin', 'bim-digital-twin', 'Collaboration space for BIM coordinators, digital twin enthusiasts and technology leads across AJi.'),
            ('Structural Engineering', 'structural-engineering', 'Discussion forum for structural engineers — codes, software, project challenges and best practices.'),
            ('Water & Environment', 'water-environment', 'Knowledge hub for AJiWE — wastewater, water networks, environment and sustainability.'),
            ('Architecture & Design', 'architecture-design', 'Inspiring design discussions, project showcases and creative exchange for AJi architects.'),
            ('HSE & Sustainability', 'hse-sustainability', 'Health, safety, environment and green building standards across AJi Group.'),
            ('Project Management', 'project-management', 'PM best practices, FIDIC, Primavera tips and lessons learned from AJi projects worldwide.'),
        ]
        for name, slug, desc in communities:
            Community.objects.get_or_create(slug=slug, defaults={'name': name, 'description': desc})
        self.stdout.write('  Communities ✓')

    # ── POLICIES ──────────────────────────────────────────────────────────────

    def _seed_policies(self):
        from portal.models import Policy
        policies = [
            ('Travel & Expense Policy', 'Guidelines for business travel, expense claims and reimbursements.', 'HR'),
            ('IT Security Policy', 'Rules for use of company IT assets, passwords and data protection.', 'IT'),
            ('Recruitment & Selection Policy', 'Procedures for fair and transparent recruitment across AJi Group.', 'HR'),
            ('Training & Development Policy', 'AJi\'s commitment to employee learning, upskilling and certification support.', 'HR'),
            ('Performance Management Policy', 'Framework for goal-setting, appraisals and performance improvement.', 'HR'),
            ('Anti-Harassment Policy', 'Zero tolerance for harassment, bullying or discrimination of any kind.', 'HR'),
            ('BIM Execution Plan (BEP)', 'AJi standard BIM Execution Plan — templates, workflows and CDE procedures.', 'Technical'),
            ('Quality Management SOP', 'Standard operating procedure for QA/QC on AJi projects.', 'Technical'),
            ('Health & Safety Policy', 'AJi HSE policy and site safety procedures for all projects.', 'HSE'),
            ('Remote Work Policy', 'Guidelines for working from home and hybrid arrangements.', 'HR'),
        ]
        for i, (title, desc, cat) in enumerate(policies):
            Policy.objects.get_or_create(title=title, defaults={'description': desc, 'category': cat, 'order': i})
        self.stdout.write('  Policies ✓')

    # ── CALENDAR EVENTS ───────────────────────────────────────────────────────

    def _seed_events(self):
        from content.models import CalendarEvent
        import datetime
        today = datetime.date.today()
        events = [
            ('Eid Adha Holiday', today.replace(month=7, day=26) if today.month <= 7 else today, 'holiday', True, ''),
            ('Town Hall — H1 Results', today.replace(month=6, day=12) if today.month <= 6 else today, 'company', True, 'HQ Auditorium'),
            ('AJi Group Safety Day', today.replace(month=8, day=15) if today.month <= 8 else today, 'company', True, 'All Sites'),
            ('Performance Review Period', today.replace(month=9, day=1) if today.month <= 9 else today, 'other', True, ''),
        ]
        for title, d, etype, all_day, loc in events:
            CalendarEvent.objects.get_or_create(title=title, date=d, defaults={
                'event_type': etype, 'all_day': all_day, 'location': loc
            })
        self.stdout.write('  Calendar events ✓')

    # ── NEWS ──────────────────────────────────────────────────────────────────

    def _seed_news(self):
        from content.models import NewsCategory, NewsArticle
        import datetime
        today = datetime.date.today()

        cat, _ = NewsCategory.objects.get_or_create(name='Company News', defaults={'slug': 'company-news'})
        env_cat, _ = NewsCategory.objects.get_or_create(name='Sustainability', defaults={'slug': 'sustainability'})

        articles = [
            ('AJi Group Wins MENA Infrastructure Award 2025', cat,
             'AJi Group has been recognised as the leading infrastructure consultancy in MENA for the fourth consecutive year, acknowledging excellence in water management, transportation and urban development projects.',
             today),
            ('AJi Launches Enhanced Wellness Program for 2026', cat,
             'Following feedback from our annual employee survey, AJi is expanding the Wellness Program to include mental health workshops, a new gym partnership with KeepUp, and the inaugural AJi Walk — a 5km charity event.',
             today),
            ('Sustainability Focus: ISO 14001 Certification Drive', env_cat,
             'AJi Group is pursuing ISO 14001 Environmental Management System certification across all business units by Q4 2026. All project teams are requested to participate in the upcoming awareness sessions.',
             today),
        ]
        for title, cat_obj, body, d in articles:
            slug = slugify(title)[:50]
            NewsArticle.objects.get_or_create(slug=slug, defaults={
                'title': title, 'category': cat_obj, 'body': body,
                'date': d, 'published': True, 'author_name': 'AJi Communications'
            })
        self.stdout.write('  News ✓')

    # ── SUPERUSER ─────────────────────────────────────────────────────────────

    def _seed_superuser(self):
        from core.models import UserProfile
        if not User.objects.filter(username='admin').exists():
            user = User.objects.create_superuser('admin', 'admin@ajigroup.com', 'admin123')
            UserProfile.objects.get_or_create(user=user, defaults={'role': 'super_admin'})
            self.stdout.write('  Superuser: admin / admin123 ✓')
        else:
            self.stdout.write('  Superuser already exists ✓')
