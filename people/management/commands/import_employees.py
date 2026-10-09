# people/management/commands/import_employees.py
"""
Import employee data from the AJi Group Excel file into the database.

Usage:
    python manage.py import_employees --file /path/to/AJi_Employee_Data_List.xlsx
    python manage.py import_employees --file /path/to/file.xlsx --dry-run
    python manage.py import_employees --file /path/to/file.xlsx --clear
    python manage.py import_employees --file /path/to/file.xlsx --skip-users

Options:
    --file      Path to the Excel file (required)
    --dry-run   Preview what would be imported without touching the database
    --clear     Delete all existing employees/departments before importing
    --skip-users  Do not create Django User accounts for employees
    --sheet     Sheet name to read from (default: 'Consolidated-8 entities')

The Excel is expected to have the following columns starting from row 3:
  Entity | Location | Employee Code | Employee Name | Department |
  Section | Division | Site / Project | Position | Birth Date |
  Direct Manager Code | Direct Manager
"""

import os
import re
import sys
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


# ── helpers ──────────────────────────────────────────────────────────────────

ENTITY_MAP = {
    'AJi':     ('AJi Group (HQ)',         'AJi HQ'),
    'AJiBE':   ('AJ Built Environment',    'AJiBE'),
    'AJiWE':   ('AJi Water & Environment', 'AJiWE'),
    'AJiSaudi':('AJi Saudi',              'AJi Saudi'),
    'HDP':     ('HDP (UAE)',              'HDP'),
    'Oman':    ('AJi Oman',              'AJi Oman'),
    'Limana':  ('Limana',                'Limana'),
    'Raseel':  ('Raseel',                'Raseel'),
}

COUNTRY_MAP = {
    'AJi':      'Jordan',
    'AJiBE':    'Jordan',
    'AJiWE':    'Jordan',
    'AJiSaudi': 'Saudi Arabia',
    'HDP':      'UAE',
    'Oman':     'Oman',
    'Limana':   'Jordan',
    'Raseel':   'Jordan',
}


def slugify_email(full_name):
    """Generate a deterministic email from a full name."""
    clean = re.sub(r"[^a-zA-Z\s]", '', full_name).strip().lower()
    parts = clean.split()
    if len(parts) >= 2:
        return f"{parts[0]}.{parts[-1]}@ajigroup.com"
    return f"{clean.replace(' ', '.')}@ajigroup.com"


def make_username(full_name, emp_code, entity_code):
    """Generate a unique Django username: firstname.lastname.code"""
    clean = re.sub(r"[^a-zA-Z\s]", '', full_name).strip().lower()
    parts = clean.split()
    if len(parts) >= 2:
        base = f"{parts[0]}.{parts[-1]}"
    else:
        base = clean.replace(' ', '.')
    # append code to guarantee uniqueness across entities
    return f"{base}.{emp_code}"[:150]


def parse_birthday(dt_val):
    """Return MM-DD string from a datetime or None."""
    if not dt_val:
        return ''
    try:
        if isinstance(dt_val, datetime):
            return dt_val.strftime('%m-%d')
        # Sometimes openpyxl returns a date string
        parsed = datetime.strptime(str(dt_val)[:10], '%Y-%m-%d')
        return parsed.strftime('%m-%d')
    except Exception:
        return ''


def parse_birth_year(dt_val):
    """Return the year as int, or None."""
    if not dt_val:
        return None
    try:
        if isinstance(dt_val, datetime):
            return dt_val.year
        parsed = datetime.strptime(str(dt_val)[:10], '%Y-%m-%d')
        return parsed.year
    except Exception:
        return None


# ── command ───────────────────────────────────────────────────────────────────

class Command(BaseCommand):
    help = 'Import employees from the AJi Group Excel file'

    def add_arguments(self, parser):
        parser.add_argument(
            '--file', '-f',
            required=True,
            help='Path to the Excel (.xlsx) file',
        )
        parser.add_argument(
            '--sheet',
            default='Consolidated-8 entities',
            help='Sheet name to read (default: "Consolidated-8 entities")',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview only — do not write to the database',
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Delete existing employees and departments before importing',
        )
        parser.add_argument(
            '--skip-users',
            action='store_true',
            help='Do not create Django User accounts',
        )

    # ── entry point ───────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        filepath = options['file']
        sheet_name = options['sheet']
        dry_run = options['dry_run']
        clear = options['clear']
        skip_users = options['skip_users']

        # ── validate file ──────────────────────────────────────────────────
        if not os.path.isfile(filepath):
            raise CommandError(f"File not found: {filepath}")
        if not filepath.lower().endswith(('.xlsx', '.xlsm')):
            raise CommandError("File must be an .xlsx or .xlsm file")

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\n{'[DRY RUN] ' if dry_run else ''}AJi Employee Import"
        ))
        self.stdout.write(f"  File  : {filepath}")
        self.stdout.write(f"  Sheet : {sheet_name}")
        self.stdout.write('')

        # ── read Excel ─────────────────────────────────────────────────────
        try:
            from openpyxl import load_workbook
        except ImportError:
            raise CommandError(
                "openpyxl is required. Install it with: pip install openpyxl"
            )

        self.stdout.write("Reading Excel file…")
        wb = load_workbook(filepath, read_only=True, data_only=True)
        if sheet_name not in wb.sheetnames:
            raise CommandError(
                f"Sheet '{sheet_name}' not found.\n"
                f"Available sheets: {', '.join(wb.sheetnames)}"
            )
        ws = wb[sheet_name]
        rows = self._read_rows(ws)
        self.stdout.write(f"  → {len(rows)} employee rows found\n")

        if dry_run:
            self._dry_run_report(rows)
            return

        # ── run inside a transaction ───────────────────────────────────────
        with transaction.atomic():
            if clear:
                self._clear_existing()
            stats = self._import(rows, skip_users=skip_users)

        self._print_stats(stats)

    # ── read rows ─────────────────────────────────────────────────────────────

    def _read_rows(self, ws):
        """
        Skip the two title rows and the header row (rows 0,1,2 in 0-index).
        Return list of dicts keyed by column name.
        """
        HEADERS = (
            'entity', 'location', 'emp_code', 'full_name',
            'department', 'section', 'division', 'site',
            'position', 'birth_date', 'mgr_code', 'mgr_name',
        )
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i < 3:       # skip title rows + header row
                continue
            # Skip completely empty rows
            if not any(row):
                continue
            record = dict(zip(HEADERS, row))
            # Normalise emp_code to string, strip whitespace
            if record.get('emp_code') is not None:
                record['emp_code'] = str(record['emp_code']).strip()
            if record.get('mgr_code') is not None:
                record['mgr_code'] = str(record['mgr_code']).strip()
            if record.get('full_name'):
                record['full_name'] = str(record['full_name']).strip()
            rows.append(record)
        return rows

    # ── dry run ───────────────────────────────────────────────────────────────

    def _dry_run_report(self, rows):
        entities  = sorted({r['entity'] for r in rows if r.get('entity')})
        depts     = sorted({r['department'] for r in rows if r.get('department')})
        divisions = sorted({r['division'] for r in rows if r.get('division')})
        sections  = sorted({r['section'] for r in rows if r.get('section')})
        positions = sorted({r['position'] for r in rows if r.get('position')})
        no_mgr    = [r for r in rows if not r.get('mgr_code')]

        self.stdout.write(self.style.SUCCESS("── Dry-run summary ──────────────────────"))
        self.stdout.write(f"  Total employees   : {len(rows)}")
        self.stdout.write(f"  Entities          : {len(entities)}  →  {', '.join(entities)}")
        self.stdout.write(f"  Departments       : {len(depts)}")
        self.stdout.write(f"  Divisions         : {len(divisions)}")
        self.stdout.write(f"  Sections (sites)  : {len(sections)}")
        self.stdout.write(f"  Unique positions  : {len(positions)}")
        self.stdout.write(f"  Without manager   : {len(no_mgr)}")
        self.stdout.write('')
        self.stdout.write("  Sample employees:")
        for r in rows[:5]:
            self.stdout.write(
                f"    [{r['entity']}] {r['full_name']} "
                f"({r['emp_code']}) — {r['position']}"
            )
        self.stdout.write('')
        self.stdout.write(self.style.WARNING(
            "Run without --dry-run to import."
        ))

    # ── clear ─────────────────────────────────────────────────────────────────

    def _clear_existing(self):
        from people.models import Employee, Department
        from django.contrib.auth.models import User

        emp_count = Employee.objects.count()
        dept_count = Department.objects.count()

        # Remove employee links from users first
        User.objects.filter(employee__isnull=False).update()
        Employee.objects.all().delete()
        Department.objects.all().delete()

        self.stdout.write(self.style.WARNING(
            f"  Cleared {emp_count} employees and {dept_count} departments."
        ))

    # ── main import ───────────────────────────────────────────────────────────

    def _import(self, rows, skip_users=False):
        from people.models import Entity, Department, Employee
        from django.contrib.auth.models import User

        stats = {
            'entities_created':    0,
            'depts_created':       0,
            'employees_created':   0,
            'employees_updated':   0,
            'users_created':       0,
            'managers_linked':     0,
            'manager_errors':      0,
            'skipped':             0,
            'warnings':            [],
        }

        # ── Step 1: Ensure entities exist ─────────────────────────────────
        self.stdout.write("Step 1/5  Syncing entities…")
        entity_cache = {}   # code → Entity instance
        for code, (name, short) in ENTITY_MAP.items():
            obj, created = Entity.objects.get_or_create(
                code=code,
                defaults={'name': name, 'short_name': short},
            )
            entity_cache[code] = obj
            if created:
                stats['entities_created'] += 1

        # ── Step 2: Collect & create departments ──────────────────────────
        self.stdout.write("Step 2/5  Syncing departments…")
        dept_cache = {}     # (dept_name, entity_code) → Department instance
        seen_depts = set()
        for r in rows:
            dept_name   = (r.get('department') or '').strip()
            entity_code = (r.get('entity') or '').strip()
            if not dept_name or not entity_code:
                continue
            key = (dept_name, entity_code)
            if key in seen_depts:
                continue
            seen_depts.add(key)
            entity_obj = entity_cache.get(entity_code)
            obj, created = Department.objects.get_or_create(
                name=dept_name,
                entity=entity_obj,
            )
            dept_cache[key] = obj
            if created:
                stats['depts_created'] += 1

        # ── Step 3: Create/update employees (no manager links yet) ────────
        self.stdout.write(f"Step 3/5  Importing {len(rows)} employees…")
        # Build a lookup: (entity_code, emp_code) → Employee
        emp_lookup = {}   # used in Step 4 for manager linking

        for r in rows:
            entity_code = (r.get('entity') or '').strip()
            emp_code    = (r.get('emp_code') or '').strip()
            full_name   = (r.get('full_name') or '').strip()
            position    = (r.get('position') or '').strip()
            birth_dt    = r.get('birth_date')
            dept_name   = (r.get('department') or '').strip()
            location    = (r.get('location') or '').strip()
            division    = (r.get('division') or '').strip()
            section     = (r.get('section') or '').strip()

            if not full_name or not emp_code:
                stats['skipped'] += 1
                stats['warnings'].append(
                    f"Skipped row — missing name or code: {r}"
                )
                continue

            entity_obj = entity_cache.get(entity_code)
            dept_obj   = dept_cache.get((dept_name, entity_code)) if dept_name else None
            country    = COUNTRY_MAP.get(entity_code, '')
            birthday   = parse_birthday(birth_dt)

            # Derive email
            email = slugify_email(full_name)

            # Extra: store division/section in role field if position is blank
            role = position
            if division and section:
                role = position  # position always has value per data check
            elif division:
                role = position

            # Unique key: (entity_code, emp_code) — codes repeat across entities
            lookup_key = (entity_code, emp_code)

            emp, created = Employee.objects.update_or_create(
                # We store emp_code in extension field for now;
                # use full_name + entity as the real lookup key
                full_name=full_name,
                entity=entity_obj,
                defaults={
                    'role':       role,
                    'department': dept_obj,
                    'email':      email,
                    'extension':  emp_code,
                    'birthday':   birthday,
                    'country':    country,
                    'is_active':  True,
                },
            )
            emp_lookup[lookup_key] = emp

            if created:
                stats['employees_created'] += 1
            else:
                stats['employees_updated'] += 1

            # ── Step 3b: Create a Django User if requested ──────────────
            if not skip_users and emp.user_id is None:
                username = make_username(full_name, emp_code, entity_code)
                # Avoid duplicate usernames
                base_username = username
                counter = 1
                while User.objects.filter(username=username).exists():
                    username = f"{base_username}_{counter}"
                    counter += 1

                name_parts = full_name.strip().split()
                first = name_parts[0] if name_parts else ''
                last  = ' '.join(name_parts[1:]) if len(name_parts) > 1 else ''

                user = User.objects.create_user(
                    username=username,
                    email=email,
                    first_name=first[:30],
                    last_name=last[:150],
                    password=None,   # unusable password — LDAP/SSO in production
                )
                user.is_active = True
                user.save()
                emp.user = user
                emp.save(update_fields=['user'])
                stats['users_created'] += 1

        # ── Step 4: Link managers ─────────────────────────────────────────
        self.stdout.write("Step 4/5  Linking managers…")

        # First pass: create placeholder Employee records for managers
        # referenced in the data but not present as employees themselves.
        # These are typically senior external hires or departed staff still
        # listed as line managers.
        ghost_lookup = {}   # mgr_code → Employee (placeholder)
        for r in rows:
            entity_code = (r.get('entity') or '').strip()
            mgr_code    = (r.get('mgr_code') or '').strip()
            mgr_name    = (r.get('mgr_name') or '').strip()
            if not mgr_code or not mgr_name:
                continue
            # Already in main employee list?
            if emp_lookup.get((entity_code, mgr_code)):
                continue
            # Already created a ghost for this code?
            if mgr_code in ghost_lookup:
                continue
            # Check any entity
            found_in_any = any(
                emp_lookup.get((ek, mgr_code)) for ek in ENTITY_MAP
            )
            if found_in_any:
                continue
            # Create ghost placeholder
            entity_obj = entity_cache.get(entity_code)
            ghost, created = Employee.objects.get_or_create(
                full_name=mgr_name,
                entity=entity_obj,
                defaults={
                    'extension':  mgr_code,
                    'role':       'Manager',
                    'country':    COUNTRY_MAP.get(entity_code, ''),
                    'is_active':  True,
                },
            )
            ghost_lookup[mgr_code] = ghost
            if created:
                stats['employees_created'] += 1

        # Second pass: link manager FK on every employee
        for r in rows:
            entity_code = (r.get('entity') or '').strip()
            emp_code    = (r.get('emp_code') or '').strip()
            mgr_code    = (r.get('mgr_code') or '').strip()
            full_name   = (r.get('full_name') or '').strip()

            if not mgr_code:
                continue

            # Resolve the employee record
            emp = emp_lookup.get((entity_code, emp_code))
            if emp is None:
                entity_obj = entity_cache.get(entity_code)
                try:
                    emp = Employee.objects.get(full_name=full_name, entity=entity_obj)
                except Employee.DoesNotExist:
                    stats['manager_errors'] += 1
                    continue

            # Resolve the manager: same entity → any entity → ghost
            mgr = emp_lookup.get((entity_code, mgr_code))
            if mgr is None:
                for ek in ENTITY_MAP:
                    mgr = emp_lookup.get((ek, mgr_code))
                    if mgr:
                        break
            if mgr is None:
                mgr = ghost_lookup.get(mgr_code)
            if mgr is None:
                # DB fallback
                mgr = Employee.objects.filter(extension=mgr_code).first()

            if mgr and emp.pk != mgr.pk:
                emp.manager = mgr
                emp.save(update_fields=['manager'])
                stats['managers_linked'] += 1
            elif mgr is None:
                stats['manager_errors'] += 1
                stats['warnings'].append(
                    f"Manager code {mgr_code} not found for {full_name} ({entity_code})"
                )

        # ── Step 5: Done ──────────────────────────────────────────────────
        self.stdout.write("Step 5/5  Finalising…")
        return stats

    # ── stats output ─────────────────────────────────────────────────────────

    def _print_stats(self, stats):
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS("── Import complete ──────────────────────"))
        self.stdout.write(f"  Entities created    : {stats['entities_created']}")
        self.stdout.write(f"  Departments created : {stats['depts_created']}")
        self.stdout.write(f"  Employees created   : {stats['employees_created']}")
        self.stdout.write(f"  Employees updated   : {stats['employees_updated']}")
        self.stdout.write(f"  Users created       : {stats['users_created']}")
        self.stdout.write(f"  Managers linked     : {stats['managers_linked']}")
        self.stdout.write(f"  Manager link errors : {stats['manager_errors']}")
        self.stdout.write(f"  Rows skipped        : {stats['skipped']}")

        if stats['warnings']:
            self.stdout.write('')
            self.stdout.write(self.style.WARNING(
                f"  {len(stats['warnings'])} warning(s) — first 10:"
            ))
            for w in stats['warnings'][:10]:
                self.stdout.write(f"    ⚠  {w}")

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS("✅  All done!"))
        self.stdout.write('')
        self.stdout.write(
            "  Note: User passwords are set to UNUSABLE (None).\n"
            "  Employees will authenticate via LDAP or manual password set.\n"
            "  To set a password: python manage.py changepassword <username>"
        )
