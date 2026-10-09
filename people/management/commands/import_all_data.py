# people/management/commands/import_all_data.py
"""
python manage.py import_all_data
Imports employees from AJi_Consolidated_Employee_Directory-sh.xlsx
- Creates Entity, Department and Employee records
- Assigns manager FK from manager_code column
- Falls back to entity-specific Hamzeh Awwad if no manager code
- Handles the CEO (Hamzeh Awwad) who appears once per entity with different codes
"""
import os
from datetime import datetime
from django.core.management.base import BaseCommand
from django.db import transaction


EXCEL_PATH = os.path.join(
    os.path.dirname(__file__),
    'AJi_Consolidated_Employee_Directory-sh.xlsx'
)

# Entity short-name normalisation
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


def parse_date(s):
    if not s or s == 'None':
        return None
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%m/%d/%Y'):
        try:
            return datetime.strptime(s.strip(), fmt).date()
        except ValueError:
            pass
    return None


class Command(BaseCommand):
    help = 'Import employees from the consolidated Excel directory'

    def add_arguments(self, parser):
        parser.add_argument('--file', default=EXCEL_PATH,
                            help='Path to the Excel file')
        parser.add_argument('--clear', action='store_true',
                            help='Delete all existing employees, entities and departments first')

    def handle(self, *args, **options):
        try:
            import openpyxl
        except ImportError:
            self.stderr.write('Run: pip install openpyxl --break-system-packages')
            return

        from people.models import Employee, Entity, Department

        file_path = options['file']
        if not os.path.isfile(file_path):
            self.stderr.write(f'File not found: {file_path}')
            self.stderr.write('Copy the xlsx next to this file or pass --file <path>')
            return

        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))

        # ── Parse all data rows ──────────────────────────────────────────────
        raw = []
        for r in rows[4:]:
            entity_raw = str(r[0]).strip() if r[0] else ''
            code       = str(r[2]).strip() if r[2] else ''
            name       = str(r[3]).strip() if r[3] else ''
            if not code or not name or not entity_raw:
                continue
            # Skip section-header rows (leading spaces = divider row)
            entity_key = entity_raw.replace(' ', '').lower()
            entity_name = ENTITY_MAP.get(entity_key, entity_raw.strip())

            raw.append({
                'entity':    entity_name,
                'office':    str(r[1]).strip() if r[1] else '',
                'code':      code,
                'name':      name,
                'dept':      str(r[4]).strip() if r[4] else '',
                'section':   str(r[5]).strip() if r[5] else '',
                'role':      str(r[6]).strip() if r[6] else '',
                'dob':       str(r[7]).strip() if r[7] else '',
                'mgr_code':  str(r[8]).strip() if r[8] else '',
                'ext':       str(r[9]).strip() if r[9] else '',
            })

        self.stdout.write(f'Parsed {len(raw)} employee records')

        # ── Build code → record lookup (per entity for safety) ───────────────
        # code is NOT globally unique — Hamzeh has different codes per entity
        # Use (entity, code) as primary key, but also a flat code→record for mgr lookup
        by_entity_code = {}
        by_code = {}  # last-write wins for cross-entity lookup
        for e in raw:
            by_entity_code[(e['entity'], e['code'])] = e
            by_code[e['code']] = e

        # Hamzeh's code per entity (for fallback manager assignment)
        hamzeh_code_per_entity = {}
        for e in raw:
            if 'hamzeh awwad' in e['name'].lower():
                hamzeh_code_per_entity[e['entity']] = e['code']
        self.stdout.write(f"Hamzeh codes per entity: {hamzeh_code_per_entity}")

        # ── Clear if requested ───────────────────────────────────────────────
        if options['clear']:
            Employee.objects.all().delete()
            Department.objects.all().delete()
            Entity.objects.all().delete()
            self.stdout.write(self.style.WARNING('Cleared existing data.'))

        # ── Pass 1: Create Entity / Department / Employee (without manager FK) ─
        with transaction.atomic():
            entity_cache = {}
            dept_cache   = {}
            emp_map      = {}   # (entity_name, code) → Employee instance

            for rec in raw:
                # Entity
                ent_name = rec['entity']
                if ent_name not in entity_cache:
                    ent, _ = Entity.objects.get_or_create(
                        name=ent_name,
                        defaults={'code': ent_name, 'short_name': ent_name}
                    )
                    entity_cache[ent_name] = ent
                entity = entity_cache[ent_name]

                # Department
                dept_key = (ent_name, rec['dept'])
                if dept_key not in dept_cache and rec['dept']:
                    dept, _ = Department.objects.get_or_create(
                        name=rec['dept'], entity=entity
                    )
                    dept_cache[dept_key] = dept
                dept = dept_cache.get(dept_key)

                # Employee
                dob = parse_date(rec['dob'])
                emp, created = Employee.objects.update_or_create(
                    emp_code=rec['code'],
                    entity=entity,
                    defaults={
                        'full_name':  rec['name'],
                        'role':       rec['role'],
                        'department': dept,
                        'extension':  rec['ext'] or '',
                        'is_active':  True,
                    }
                )
                emp_map[(ent_name, rec['code'])] = emp

            self.stdout.write(f'\nPass 1: {len(emp_map)} employees created/updated')

            # ── Pass 2: Assign manager FK ─────────────────────────────────────
            assigned = 0
            fallback = 0
            skipped  = 0

            for rec in raw:
                ent_name = rec['entity']
                emp = emp_map.get((ent_name, rec['code']))
                if not emp:
                    continue

                mgr = None
                mgr_code = rec['mgr_code']

                if mgr_code:
                    # Try same-entity first, then cross-entity
                    mgr = emp_map.get((ent_name, mgr_code))
                    if not mgr:
                        # Cross-entity lookup
                        for key, m in emp_map.items():
                            if key[1] == mgr_code:
                                mgr = m
                                break

                if mgr and mgr != emp:
                    emp.manager = mgr
                    emp.save(update_fields=['manager'])
                    assigned += 1
                elif mgr == emp:
                    # Self-referential (e.g. AJiSaudi Hamzeh) — skip
                    skipped += 1
                else:
                    # No manager code or not found — assign entity Hamzeh
                    hamzeh_code = hamzeh_code_per_entity.get(ent_name)
                    if hamzeh_code:
                        hamzeh = emp_map.get((ent_name, hamzeh_code))
                        if hamzeh and hamzeh != emp:
                            emp.manager = hamzeh
                            emp.save(update_fields=['manager'])
                            fallback += 1
                        else:
                            skipped += 1
                    else:
                        skipped += 1

            self.stdout.write(
                f'Pass 2: {assigned} assigned from code, '
                f'{fallback} assigned to entity Hamzeh, '
                f'{skipped} skipped (top-level / no code)'
            )

        self.stdout.write(self.style.SUCCESS(
            f'\nDone — {Employee.objects.count()} employees total.'
        ))
