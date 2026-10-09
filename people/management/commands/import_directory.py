# people/management/commands/import_directory.py
"""
python manage.py import_directory
Imports employees, departments, sections and skills from the consolidated Excel.

Passes:
  1. Parse & validate all rows from the Excel
  2. Create Entities, Departments, Sections
  3. Create/update Employees (without manager/section FK yet)
  4. Assign manager FK (direct from mgr_code; fallback = entity Hamzeh)
  5. Elect dept managers and section heads by most-reports count
  6. Assign section FK to every employee
  7. Import skills (Skill + EmployeeSkill with proficiency)
"""
import os
from datetime import datetime
from collections import defaultdict
from django.core.management.base import BaseCommand
from django.db import transaction

EXCEL_PATH = os.path.join(os.path.dirname(__file__), 'directory.xlsx')

ENTITY_MAP = {
    'aji':      'AJi',
    'ajibe':    'AJiBE',
    'ajisaudi': 'AJiSaudi',
    'ajiwe':    'AJiWE',
    'hdp':      'HDP',
    'limana':   'Limana',
    'oman':     'Oman',
    'raseel':   'Raseel',
}

PROFICIENCY_MAP = {
    'familiar':   1,
    'proficient': 3,
    'expert':     4,
}


def norm_entity(raw):
    return ENTITY_MAP.get(raw.replace(' ', '').lower(), raw.strip())


def parse_date(s):
    if not s or s == 'None':
        return None
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%m/%d/%Y'):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    return None


def parse_skills(raw):
    """Return list of (skill_name, level_int) from 'Name (Level), ...' string."""
    result = []
    if not raw:
        return result
    for part in raw.split(','):
        part = part.strip()
        if '(' in part and part.endswith(')'):
            name, lvl = part.rsplit('(', 1)
            name = name.strip()
            lvl  = lvl.rstrip(')').strip().lower()
            level = PROFICIENCY_MAP.get(lvl, 2)
            if name:
                result.append((name, level))
    return result


class Command(BaseCommand):
    help = 'Full import: employees + departments + sections + skills'

    def add_arguments(self, parser):
        parser.add_argument('--file', default=EXCEL_PATH, help='Path to Excel file')
        parser.add_argument('--clear', action='store_true', help='Clear all existing data first')

    def handle(self, *args, **options):
        try:
            import openpyxl
        except ImportError:
            self.stderr.write('pip install openpyxl --break-system-packages')
            return

        from people.models import (
            Entity, Department, Section, Employee,
            SkillCategory, Skill, EmployeeSkill
        )

        file_path = options['file']
        if not os.path.isfile(file_path):
            self.stderr.write(f'File not found: {file_path}')
            return

        # ── Parse Excel ──────────────────────────────────────────────────────
        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        ws = wb['Employee Directory']
        raw_rows = []
        def col(r, i, default=''):
            try:
                v = r[i]
                return str(v).strip() if v is not None else default
            except IndexError:
                return default

        for r in ws.iter_rows(values_only=True):
            entity_raw = col(r, 0)
            code       = col(r, 2)
            name       = col(r, 3)
            if not code or not name or not entity_raw:
                continue
            raw_rows.append({
                'entity':     norm_entity(entity_raw),
                'office':     col(r, 1),
                'code':       code,
                'name':       name,
                'dept':       col(r, 4),
                'section':    col(r, 5),
                'role':       col(r, 6),
                'dob':        col(r, 7),
                'mgr_code':   col(r, 8),
                'ext':        col(r, 9),
                'office':     col(r, 1),   # Office / Site column
                'skills_raw': col(r, 11),
            })

        self.stdout.write(f'Parsed {len(raw_rows)} employee rows')

        # ── Clear ─────────────────────────────────────────────────────────────
        if options['clear']:
            EmployeeSkill.objects.all().delete()
            Employee.objects.all().delete()
            Section.objects.all().delete()
            Department.objects.all().delete()
            Entity.objects.all().delete()
            from django.contrib.auth.models import User as DjangoUser
            DjangoUser.objects.filter(is_superuser=False).delete()
            self.stdout.write(self.style.WARNING('Cleared all existing data.'))

        with transaction.atomic():

            # ── Pass 1: Entities ─────────────────────────────────────────────
            entity_cache = {}
            for rec in raw_rows:
                n = rec['entity']
                if n not in entity_cache:
                    ent, _ = Entity.objects.get_or_create(
                        name=n, defaults={'code': n, 'short_name': n}
                    )
                    entity_cache[n] = ent
            self.stdout.write(f'Entities: {len(entity_cache)}')

            # ── Pass 2: Departments ───────────────────────────────────────────
            dept_cache = {}   # (entity_name, dept_name) → Department
            for rec in raw_rows:
                key = (rec['entity'], rec['dept'])
                if key not in dept_cache and rec['dept']:
                    dept, _ = Department.objects.get_or_create(
                        name=rec['dept'],
                        entity=entity_cache[rec['entity']],
                    )
                    dept_cache[key] = dept
            self.stdout.write(f'Departments: {len(dept_cache)}')

            # ── Pass 3: Sections ──────────────────────────────────────────────
            sec_cache = {}   # (entity_name, dept_name, section_name) → Section
            for rec in raw_rows:
                if not rec['section']:
                    continue
                key = (rec['entity'], rec['dept'], rec['section'])
                if key not in sec_cache:
                    dept = dept_cache.get((rec['entity'], rec['dept']))
                    if dept:
                        sec, _ = Section.objects.get_or_create(
                            name=rec['section'], department=dept
                        )
                        sec_cache[key] = sec
            self.stdout.write(f'Sections: {len(sec_cache)}')

            # ── Pass 4: Employees (no FK assignments yet) ─────────────────────
            emp_map = {}   # (entity_name, code) → Employee
            for rec in raw_rows:
                entity = entity_cache[rec['entity']]
                dept   = dept_cache.get((rec['entity'], rec['dept']))
                sec    = sec_cache.get((rec['entity'], rec['dept'], rec['section']))
                emp, _ = Employee.objects.update_or_create(
                    emp_code=rec['code'],
                    entity=entity,
                    defaults={
                        'full_name':  rec['name'],
                        'role':       rec['role'],
                        'department': dept,
                        'section':    sec,
                        'extension':  rec['ext'],
                        'office':     rec['office'],
                        'is_active':  True,
                    }
                )
                emp_map[(rec['entity'], rec['code'])] = emp
            self.stdout.write(f'Employees created/updated: {len(emp_map)}')

            # ── Pass 5: Assign manager FK ─────────────────────────────────────
            # Hamzeh per entity (fallback)
            hamzeh = {}
            for rec in raw_rows:
                if 'hamzeh awwad' in rec['name'].lower():
                    hamzeh[rec['entity']] = (rec['entity'], rec['code'])

            assigned = fallback = skipped = 0
            for rec in raw_rows:
                emp = emp_map.get((rec['entity'], rec['code']))
                if not emp:
                    continue
                mgr = None
                if rec['mgr_code']:
                    mgr = emp_map.get((rec['entity'], rec['mgr_code']))
                    if not mgr:
                        # cross-entity fallback
                        for key, m in emp_map.items():
                            if key[1] == rec['mgr_code']:
                                mgr = m
                                break
                if mgr and mgr != emp:
                    emp.manager = mgr
                    emp.save(update_fields=['manager'])
                    assigned += 1
                elif mgr == emp:
                    skipped += 1
                else:
                    hkey = hamzeh.get(rec['entity'])
                    h = emp_map.get(hkey) if hkey else None
                    if h and h != emp:
                        emp.manager = h
                        emp.save(update_fields=['manager'])
                        fallback += 1
                    else:
                        skipped += 1

            self.stdout.write(
                f'Manager assignment: {assigned} direct, '
                f'{fallback} → entity Hamzeh, {skipped} top-level'
            )

            # ── Pass 6: Elect dept managers by most-reports ───────────────────
            dept_votes = defaultdict(lambda: defaultdict(int))
            for rec in raw_rows:
                if rec['mgr_code'] and rec['dept']:
                    dk = (rec['entity'], rec['dept'])
                    dept_votes[dk][rec['mgr_code']] += 1

            dept_mgr_count = 0
            for dk, votes in dept_votes.items():
                top_code = max(votes, key=votes.get)
                mgr_emp = emp_map.get((dk[0], top_code))
                dept    = dept_cache.get(dk)
                if mgr_emp and dept:
                    dept.manager = mgr_emp
                    dept.save(update_fields=['manager'])
                    dept_mgr_count += 1
            self.stdout.write(f'Dept managers assigned: {dept_mgr_count}')

            # ── Pass 7: Elect section heads by most-reports ───────────────────
            sec_votes = defaultdict(lambda: defaultdict(int))
            for rec in raw_rows:
                if rec['mgr_code'] and rec['section']:
                    sk = (rec['entity'], rec['dept'], rec['section'])
                    sec_votes[sk][rec['mgr_code']] += 1

            sec_head_count = 0
            for sk, votes in sec_votes.items():
                top_code = max(votes, key=votes.get)
                head_emp = emp_map.get((sk[0], top_code))
                sec      = sec_cache.get(sk)
                if head_emp and sec:
                    sec.head = head_emp
                    sec.save(update_fields=['head'])
                    sec_head_count += 1
            self.stdout.write(f'Section heads assigned: {sec_head_count}')

            # ── Pass 8: Create Django Users ───────────────────────────────────
            from django.contrib.auth.models import User as DjangoUser

            import re as _re
            def _slug(s): return _re.sub(r'[^a-z0-9]', '', s.lower())

            user_created = user_linked = 0
            for rec in raw_rows:
                emp = emp_map.get((rec['entity'], rec['code']))
                if not emp or emp.user_id:
                    user_linked += (1 if emp and emp.user_id else 0)
                    continue

                name_parts = rec['name'].strip().split()
                first  = name_parts[0]          if name_parts          else 'user'
                last   = name_parts[-1]         if len(name_parts) > 1 else first
                code   = rec['code'].strip()

                username = f"{_slug(first)}.{_slug(last)}.{_slug(code)}" if code \
                           else f"{_slug(first)}.{_slug(last)}"
                username = username[:150]

                base, n = username, 1
                while DjangoUser.objects.filter(username=username).exists():
                    username = f'{base}_{n}'
                    n += 1

                email = f"{_slug(first)}.{_slug(last)}@ajigroup.com"
                first_name = name_parts[0][:30]               if name_parts          else ''
                last_name  = ' '.join(name_parts[1:])[:150]   if len(name_parts) > 1 else ''

                user = DjangoUser(
                    username=username, email=email,
                    first_name=first_name, last_name=last_name, is_active=True,
                )
                user.set_unusable_password()
                user.save()

                emp.user = user
                emp.save(update_fields=['user'])
                user_created += 1

            self.stdout.write(f'Users created: {user_created}, already linked: {user_linked}')

            # ── Pass 9: Skills ────────────────────────────────────────────────
            # Single "General" skill category (or create per-skill categories later)
            skill_cat, _ = SkillCategory.objects.get_or_create(
                name='Professional Skills', defaults={'order': 1}
            )
            skill_cache = {}   # skill_name → Skill

            EmployeeSkill.objects.all().delete()   # fresh import each time

            skill_rows = 0
            for rec in raw_rows:
                emp = emp_map.get((rec['entity'], rec['code']))
                if not emp or not rec['skills_raw']:
                    continue
                for skill_name, level in parse_skills(rec['skills_raw']):
                    if skill_name not in skill_cache:
                        skill = Skill.objects.filter(name=skill_name).first()
                        if not skill:
                            skill = Skill.objects.create(
                                name=skill_name, category=skill_cat
                            )
                        skill_cache[skill_name] = skill
                    EmployeeSkill.objects.get_or_create(
                        employee=emp,
                        skill=skill_cache[skill_name],
                        defaults={'level': level}
                    )
                    skill_rows += 1

            self.stdout.write(f'Skills imported: {skill_rows} employee-skill records, {len(skill_cache)} unique skills')

        self.stdout.write(self.style.SUCCESS(
            f'\nDone.\n'
            f'  Entities:    {Entity.objects.count()}\n'
            f'  Departments: {Department.objects.count()}\n'
            f'  Sections:    {Section.objects.count()}\n'
            f'  Employees:   {Employee.objects.count()}\n'
            f'  Users:       {Employee.objects.exclude(user=None).count()} linked\n'
            f'  Skills:      {Skill.objects.count()}\n'
            f'  Emp-Skills:  {EmployeeSkill.objects.count()}'
        ))
