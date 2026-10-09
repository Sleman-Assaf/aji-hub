# portal/management/commands/seed_hr_content.py
"""
python manage.py seed_hr_content
Seeds OnboardingHandbookItem, WellnessProgram, ChildcarePartner and
GrowthCertification with the content that used to be hardcoded in views.py.
Run once to populate the DB so HR group members can manage it from the UI.
"""
from django.core.management.base import BaseCommand


ONBOARDING_DATA = {
    'conduct': [
        ('Respectful Workplace', 'Treat colleagues, clients and partners with respect and dignity, avoid any harassment or discrimination, and contribute to a positive, collaborative culture. Behaviour that undermines this may result in disciplinary action.'),
        ('Equal Opportunity', 'AJi is an equal-opportunity employer — decisions are based on merit, qualifications and business needs, with no discrimination on gender, nationality, religion, age or any protected characteristic.'),
        ('Conflict of Interest', 'Avoid activities or interests (memberships, directorships, shares, contracts) that conflict with AJi. Notify your manager immediately; outside work needs direct-manager and HR approval.'),
        ('Confidentiality', 'Confidential information must not be used or disclosed during or after your employment. A breach is a serious matter and may be grounds for dismissal.'),
    ],
    'att': [
        ('Working hours', '08:00 – 17:00. Overtime is unpaid unless pre-approved by your direct manager via a request on Mena-me.'),
        ('Punching In / Out', 'Record every entry and exit via the clock at the AJi building reception — this drives salary, overtime, leave and vacation records.'),
        ('Overtime', 'Subject to workload and prior approval from the department head.'),
        ('Lunch break', '15 minutes, between 13:30 and 14:00.'),
        ('Ramadan timings', 'At the discretion of management.'),
        ('Severe weather', "Following the announcement of the Prime Minister's office."),
    ],
    'leave': [
        ('Annual vacation', 'As per your employment contract — subject to prior online approval.'),
        ('Sick leave', 'One day can be reported by phone; more than one day needs a medical report. Apply via Mena-me.'),
        ('Official leave', 'As required by the job.'),
        ('Personal leave', 'Taken during working hours; deducted from your vacation balance.'),
        ('Paternity leave', '3 days, per Jordanian company law.'),
        ('Grief leave', '3 days for an immediate family member (parent, child, spouse, sibling).'),
        ('Marriage leave', '3 days, after one year with the company.'),
        ('Haj leave', '10 working days after 5 years — once during employment.'),
        ('Maternity leave', '70 days, per Jordanian company law.'),
    ],
    'prof': [
        ('Health & Safety', 'Report hazards, near-misses and accidents, look out for colleagues, and record incidents in the accident register on Mena System.'),
        ('Buildings Security', 'Cameras cover most accesses. Use main doors; visitors are logged, badged and escorted. Fire exits are for emergencies only.'),
        ('Car Parking', "Up to 40 spaces to the right of the entrance — park appropriately without blocking neighbours' garages."),
        ('Business Cards', 'Available to management after probation (HR approval); other roles need a department-head recommendation.'),
        ('Computer Security', 'Never share your password, shut down your PC when leaving, and protect it from damage and spillage.'),
        ('Internet Use', 'Only work-appropriate sites — browsing or downloading non-work content may affect performance and lead to disciplinary action.'),
        ('Smoking', 'Non-smoking building; smoking only in outdoor designated areas, kept clean.'),
        ('Dress Code', 'Formal and smart-casual — clean and neat, appropriate to your role, clients and culture.'),
        ('Prayer Rooms & Ablutions', 'Prayer rooms and ablution areas are provided; please use ablution areas rather than public toilets.'),
    ],
    'comp': [
        ('Salary', 'Confidential and paid into a company-approved bank account, with a full breakdown on your online payslip.'),
        ('Annual review', 'Salary is reviewed annually, subject to your performance, manpower availability and market conditions.'),
        ('Deductions', 'JEA membership (engineers only), training policy, mobile phone bills (if applicable), payroll tax and the employee share of Social Security. No deductions are made without prior notice.'),
        ('Medical insurance', 'Class A via MedNet after probation — 15% employee contribution on outpatient; inpatient fully covered.'),
        ('Life insurance', 'Company-provided life cover for all employees.'),
        ('Social Security', 'Enrolled per Jordanian law, with employer and employee contributions.'),
    ],
    'perf': [
        ('Probationary review', '90 days after joining; confirmation follows a satisfactory evaluation and an employee-experience interview.'),
        ('Annual performance review', 'Conducted by your direct manager to review performance, set goals and plan training each year.'),
        ('Disciplinary review', 'For misconduct, a documented note is filed for a limited time with clear corrective actions.'),
        ('Complaint procedure', 'File a complaint against a colleague or manager with HR, who will take the necessary steps to resolve it.'),
    ],
    'exit': [
        ('During probation', 'Either party may end employment at any time during probation, without notice or reason.'),
        ('Resignation', "Resign at any time with a minimum of one month's notice."),
        ('Termination', 'For cause (bylaw violations or performance — verbal, then three written); otherwise subject to compensation per labour law.'),
        ('Return of property', 'Return all company property and hand over current work and confidential notes to authorised people.'),
        ('Exit interview', 'A confidential interview in your last week to help us improve the organisation.'),
        ('References', "You're entitled to a record-of-service letter; reference letters are signed by your department head."),
    ],
}

WELLNESS_DATA = [
    ('Lose to Win Competition', 'A friendly weight-loss challenge encouraging a healthier lifestyle, with employees from different departments taking part.', 'Sep 2024'),
    ('Gym Corporate Rates', 'Exclusive corporate rates with Hive Gym and KeepUp Gym to encourage fitness and support overall wellbeing.', ''),
    ('Mental Health Workshops', 'Sessions with psychologists on managing workplace stress and building resilience — plus a corporate rate with psychologist Deema Haddad.', 'World Mental Health Day · 2023–2025'),
    ('Nutrition Awareness Sessions', 'Workshops on healthy eating habits and meal planning, with an accredited holistic nutritionist sharing tips in support groups.', 'Live Cooking · 2024–2025'),
    ('AJi Football League', 'An interdepartmental league played on weekends, fostering a fun culture and stronger team relationships.', "Men's League · Jun 2024"),
    ('Breast Cancer Awareness & Checkups', 'October awareness sessions with consultants and survivors, plus free checkups for female employees.', 'Pink October 2023–2025'),
    ('Medical Day', 'An annual on-premises health screening including blood pressure, glucose and cholesterol checks.', ''),
    ('AJi Walk', 'Regular group walking sessions in parks and suburbs that promote activity, social interaction and team building.', '2025'),
    ('AJi Steps Challenge', 'Employees track daily steps through a dedicated app, set goals and compete for the highest count, with rewards for top performers.', 'Oct 2025'),
    ('Ergonomics Interactive Session', 'Practical guidance and exercises on healthy work habits to reduce physical strain and prevent back pain and fatigue.', ''),
]

CHILDCARE_PARTNERS = [
    'Little Einsteins', 'play2learn', 'Kids Allowed', 'Iman Early Learning',
    'Bright Beginnings', 'StarFish Nursery', 'Teddybear Time', 'Banan DayCare',
    'Sparkle Nursery', 'Here to Grow', 'Bumble Bee', 'Biara Academy',
    'Kidstoria', 'ABC Childcare', 'The Crayon Box', 'Alraya Nursery', 'SMART',
]

CERTIFICATIONS = ['PMP', 'LEED', 'FIDIC', 'PMP-CP', 'OSHA', 'RICS', 'PMI-SP']


class Command(BaseCommand):
    help = 'Seed OnboardingHandbookItem, WellnessProgram, ChildcarePartner, GrowthCertification'

    def add_arguments(self, parser):
        parser.add_argument('--clear', action='store_true', help='Delete existing data first')

    def handle(self, *args, **options):
        from portal.models import OnboardingHandbookItem, WellnessProgram, ChildcarePartner, GrowthCertification

        if options['clear']:
            OnboardingHandbookItem.objects.all().delete()
            WellnessProgram.objects.all().delete()
            ChildcarePartner.objects.all().delete()
            GrowthCertification.objects.all().delete()
            self.stdout.write(self.style.WARNING('Cleared existing HR content.'))

        # Onboarding items
        count = 0
        for tab, items in ONBOARDING_DATA.items():
            for i, (title, body) in enumerate(items):
                OnboardingHandbookItem.objects.get_or_create(
                    tab=tab, title=title, defaults={'body': body, 'order': i}
                )
                count += 1
        self.stdout.write(f'  Onboarding items: {OnboardingHandbookItem.objects.count()} (from {count} seed entries)')

        # Wellness programs
        for i, (name, desc, period) in enumerate(WELLNESS_DATA):
            WellnessProgram.objects.get_or_create(
                name=name, defaults={'description': desc, 'period': period, 'order': i}
            )
        self.stdout.write(f'  Wellness programs: {WellnessProgram.objects.count()}')

        # Childcare partners
        for i, name in enumerate(CHILDCARE_PARTNERS):
            ChildcarePartner.objects.get_or_create(name=name, defaults={'order': i})
        self.stdout.write(f'  Childcare partners: {ChildcarePartner.objects.count()}')

        # Certifications
        for i, name in enumerate(CERTIFICATIONS):
            GrowthCertification.objects.get_or_create(name=name, defaults={'order': i})
        self.stdout.write(f'  Certifications: {GrowthCertification.objects.count()}')

        self.stdout.write(self.style.SUCCESS('\nAll HR content seeded.'))
        self.stdout.write('Run:  python manage.py seed_hr_content')
        self.stdout.write('Or:   python manage.py seed_hr_content --clear  (to reset)')
