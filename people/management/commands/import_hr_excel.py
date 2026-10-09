# people/management/commands/import_hr_excel.py
"""Import the HR consolidated employee list (Excel) into the Emp_HR table.

    python manage.py import_hr_excel "C:\\path\\to\\Emp-list.xlsx"
    python manage.py import_hr_excel Emp-list.xlsx --no-feed   # import only

Reads by HEADER NAME, not column position, so HR can reorder or add columns
freely; the header row is found wherever it sits. What the sheet names is
resolved into the org graph itself: Entity, Department (scoped to its
entity), and Section (scoped to its department) rows are found or created —
the same rows the Employee table points at — and each person's manager
becomes a link to the manager's own HR row.

Email is the unique index between HR and AD: the sheet's Employee Email
column is used where filled (normalized to the real domain), derived from
the name (First_Last@ajigroup.com — the same rule logins follow) where
empty, and left blank only where two people would collide on it.

Rows are upserted by (entity, code) across re-imports: joiners appear,
changed titles update, and rows HR removed are deleted from Emp_HR (never
from Employee). After the import the feed runs (people/hr_feed.py): AD wins
where AD wrote a value, HR fills the gaps, names always follow HR spelling.
"""
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from people.models import Department, EmpHR, Entity, Section

#: Sheet header -> record key. Matching is case-insensitive and ignores
#: spaces and punctuation, so 'Site / Project' and 'site project' both land.
HEADERS = {
    'entity': 'entity_text',
    'location': 'location',
    'employeecode': 'emp_code',
    'employeename': 'full_name',
    'department': 'department_text',
    'section': 'section_text',
    'division': 'division',
    'siteproject': 'site_project',
    'position': 'position',
    'birthdate': 'birth_date',
    'directmanagercode': 'manager_code',
    'directmanager': 'manager_name',
    'email': 'email',
    'emailaddress': 'email',
    'employeeemail': 'email',
    'country': 'country',
}


def _norm(text):
    return ''.join(ch for ch in str(text or '').lower() if ch.isalnum())


def _text(value):
    """Cells arrive as whatever Excel stored: 8043 the number, '0092' the
    text, None. Everything becomes a clean string — 8043.0 without the .0,
    leading zeros kept when they were text."""
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


class Command(BaseCommand):
    help = 'Import the HR employee list Excel into Emp_HR and feed Employee gaps.'

    def add_arguments(self, parser):
        parser.add_argument('file', help='Path to the HR Excel file.')
        parser.add_argument('--sheet', default='', help='Sheet name (default: first).')
        parser.add_argument('--no-feed', action='store_true',
                            help='Import only; skip feeding Employee.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Parse and report without writing.')
        parser.add_argument('--create-missing', action='store_true',
                            help='After importing, create a login-less '
                                 'Employee for every HR person with none.')

    def handle(self, *args, **opts):
        path = Path(opts['file'])
        if not path.exists():
            raise CommandError(f'{path} not found.')
        try:
            import openpyxl
        except ImportError:
            raise CommandError("openpyxl is not installed (it is in requirements.txt).")
        from people.hr_feed import _fix_domain, _mail_domain, _department

        wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
        ws = wb[opts['sheet']] if opts['sheet'] else wb.worksheets[0]

        rows = ws.iter_rows(values_only=True)
        columns = None
        for row in rows:
            normed = [_norm(c) for c in row]
            if 'employeename' in normed:
                columns = {i: HEADERS[n] for i, n in enumerate(normed)
                           if n in HEADERS}
                break
        if not columns:
            raise CommandError('No header row with "Employee Name" found — '
                               'is this the HR consolidated list?')
        self.stdout.write('Columns recognised: '
                          + ', '.join(sorted(set(columns.values()))))

        parsed, skipped = [], 0
        for row in rows:                      # continues after the header
            record = {}
            for idx, field in columns.items():
                value = row[idx] if idx < len(row) else None
                if field == 'birth_date':
                    if isinstance(value, datetime):
                        value = value.date()
                    elif not isinstance(value, date):
                        value = None
                    record[field] = value
                elif field == 'email':
                    record[field] = _fix_domain(_text(value).lower()) or None
                else:
                    record[field] = _text(value)
            if not record.get('full_name') or not record.get('emp_code'):
                skipped += 1
                continue
            parsed.append(record)

        # Email, the unique index: the sheet's address where given, the
        # derived First_Last form where not. Collisions — inside the sheet or
        # between a derived and a given address — leave the later rows blank
        # rather than lying; a sheet-provided address always outranks a
        # derived one for the same string.
        domain = _mail_domain()

        def _derived(name):
            words = [w for w in str(name).split() if any(c.isalpha() for c in w)]
            if len(words) < 2:
                return None
            return f'{words[0].lower()}_{words[-1].lower()}@{domain}'

        given = Counter(r['email'] for r in parsed if r.get('email'))
        for r in parsed:
            if r.get('email') and given[r['email']] > 1:
                r['email'] = None            # duplicate in the sheet itself
        taken = {r['email'] for r in parsed if r.get('email')}
        derived_n = 0
        candidates = Counter(_derived(r['full_name'])
                             for r in parsed if not r.get('email'))
        for r in parsed:
            if r.get('email'):
                continue
            mail = _derived(r['full_name'])
            if mail and candidates[mail] == 1 and mail not in taken:
                r['email'] = mail
                derived_n += 1
            else:
                r['email'] = None
        with_mail = sum(1 for r in parsed if r['email'])
        self.stdout.write(
            f'{len(parsed)} people parsed, {skipped} rows skipped; '
            f'{with_mail} carry an email ({derived_n} derived from names, '
            f'{len(parsed) - with_mail} ambiguous or short, left empty).')

        keys = {(r['entity_text'], r['emp_code']) for r in parsed}
        if len(keys) != len(parsed):
            raise CommandError('Duplicate (entity, code) pairs inside the '
                               'sheet — fix the file and re-run.')
        if opts['dry_run']:
            self.stdout.write(self.style.WARNING('Dry run — nothing written.'))
            return

        created = updated = 0
        with transaction.atomic():
            # The org graph the rows hang from — the SAME rows Employee uses.
            entities, departments, sections = {}, {}, {}

            def _entity(name):
                if not name:
                    return None
                if name not in entities:
                    entities[name], _ = Entity.objects.get_or_create(
                        code=name[:30], defaults={'name': name})
                return entities[name]

            def _dept(name, entity):
                if not name:
                    return None
                key = (name, entity.pk if entity else None)
                if key not in departments:
                    departments[key] = _department(name, entity)
                return departments[key]

            def _sect(name, dept):
                # Section requires its department; a section named without
                # one has nowhere to hang and stays text-only (division/site
                # columns keep such context).
                if not name or dept is None:
                    return None
                key = (name, dept.pk)
                if key not in sections:
                    sections[key], _ = Section.objects.get_or_create(
                        name=name[:200], department=dept)
                return sections[key]

            existing = {(h.entity.code if h.entity else '', h.emp_code): h
                        for h in EmpHR.objects.select_related('entity')}
            by_key = {}
            for r in parsed:
                entity = _entity(r['entity_text'])
                dept = _dept(r.get('department_text', ''), entity)
                values = {
                    'email': r['email'],
                    'entity': entity,
                    'location': r.get('location', '')[:100],
                    'full_name': r['full_name'][:200],
                    'department': dept,
                    'section': _sect(r.get('section_text', ''), dept),
                    'division': r.get('division', '')[:200],
                    'site_project': r.get('site_project', '')[:200],
                    'position': r.get('position', '')[:200],
                    'birth_date': r.get('birth_date'),
                    'country': r.get('country', '')[:100],
                    'manager_code': r.get('manager_code', '')[:30],
                    'manager_name': r.get('manager_name', '')[:200],
                }
                key = (r['entity_text'], r['emp_code'])
                hr = existing.pop(key, None)
                if hr is None:
                    hr = EmpHR.objects.create(emp_code=r['emp_code'][:30],
                                              **values)
                    created += 1
                else:
                    dirty = [f for f, v in values.items()
                             if getattr(hr, f) != v]
                    if dirty:
                        for f in dirty:
                            setattr(hr, f, values[f])
                        hr.save()
                        updated += 1
                by_key[key] = hr

            # Managers: each row linked to its manager's own HR row, matched
            # by code within the same entity's numbering.
            linked = 0
            for r in parsed:
                mcode = r.get('manager_code', '')
                if not mcode:
                    continue
                hr = by_key[(r['entity_text'], r['emp_code'])]
                boss = by_key.get((r['entity_text'], mcode))
                if boss is not None and boss.pk != hr.pk and hr.manager_id != boss.pk:
                    hr.manager = boss
                    hr.save(update_fields=['manager'])
                    linked += 1

            # Rows HR no longer lists: gone from the mirror. Employee is
            # never touched by a removal — leavers are AD's call.
            removed = len(existing)
            if existing:
                EmpHR.objects.filter(
                    pk__in=[h.pk for h in existing.values()]).delete()

        self.stdout.write(self.style.SUCCESS(
            f'Emp_HR: {created} created, {updated} updated, {removed} removed, '
            f'{linked} managers linked ({EmpHR.objects.count()} total).'))

        if not opts['no_feed']:
            from people.hr_feed import apply_hr
            report = apply_hr(create_missing=opts['create_missing'])
            self.stdout.write(self.style.SUCCESS('Feed: ' + report['message']))
            if report['unmatched_names']:
                self.stdout.write('  unmatched (no Employee found): '
                                  + ', '.join(report['unmatched_names'][:10])
                                  + (' …' if report['unmatched'] > 10 else ''))
