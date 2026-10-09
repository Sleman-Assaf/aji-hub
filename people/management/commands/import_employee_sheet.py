"""Bring names, positions and reporting lines in from the HR spreadsheets.

    manage.py import_employee_sheet <file.xlsx> [more.xlsx ...] [--dry-run]

The consolidated sheet is the authority. It carries a position for all 1,251
people and a direct manager code for 1,122 of them, where the database had a
manager on 57 — which is why the org chart drew 160 people hanging off the CEO.

Later files override earlier ones, so a corrections sheet can be passed after
the full list.

Names are cut to a first name and a surname, in ordinary case. The sheets hold
"Dana Hasan Mohammad Shannaq" and "ABDULLAH KHALAF ABDULLAH A"; neither belongs
on a card.
"""
import pathlib
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from people.models import Employee

#: The header row differs between files — the consolidated sheet has two title
#: rows above it — so it is found rather than assumed.
WANTED = ('employee code', 'employee name', 'position')


def title_word(word):
    """"ABDULLAH" -> "Abdullah", "AL-ANAZI" -> "Al-Anazi", "o'neill" -> "O'Neill".

    Each run of letters is capitalised where it sits, so whatever separates them
    survives. Doing it as a pass per separator does not work: splitting on "-"
    fixes "Al-Anazi" and the following pass on "'" lowercases everything after
    its first letter again, leaving "Al-anazi".
    """
    return re.sub(r'[A-Za-z؀-ۿ]+',
                  lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(),
                  str(word))


#: Words that belong to the surname that follows them. Dropping these turns
#: "Ban Abu Hammour" into "Ban Hammour" and "Mayyas Al Subeihi" into "Mayyas
#: Subeihi", which is not a shortening of the name but a different one.
PARTICLES = {'al', 'el', 'abu', 'abd', 'abdel', 'abdul', 'bin', 'ben', 'ibn',
             'bint', 'van', 'von', 'de', 'da', 'del', 'della', 'la', 'le', 'st'}


def short_name(raw):
    """A first name and a surname, in ordinary case.

    Middle names go. A trailing initial goes with them — "Abdullah Khalaf
    Abdullah A" ends in a letter that is not a surname, so the walk back skips
    it. Stray punctuation the sheets carry ("Ban Munzer . Haddadin") is dropped
    rather than becoming a name of its own. A particle immediately before the
    surname stays with it.
    """
    if not raw:
        return ''
    parts = [p for p in re.split(r'\s+', str(raw).replace('\xa0', ' ').strip()) if p]
    parts = [p for p in parts if re.search(r'[A-Za-z؀-ۿ]', p)]
    if not parts:
        return ''
    first = parts[0]
    # The last part long enough to be a surname rather than an initial.
    tail, at = '', None
    for i in range(len(parts) - 1, 0, -1):
        if len(re.sub(r'[^A-Za-z؀-ۿ]', '', parts[i])) >= 2:
            tail, at = parts[i], i
            break
    words = [first]
    if tail:
        # "… Al Subeihi" — the particle is part of the surname, not a middle name.
        if at is not None and at - 1 > 0 and parts[at - 1].lower().strip('.') in PARTICLES:
            words.append(parts[at - 1])
        words.append(tail)
    return ' '.join(title_word(w) for w in words)


def code_key(value):
    """Employee codes are compared without their leading zeros: the sheets write
    0096 where the database holds 96, and they are the same person."""
    text = str(value or '').strip()
    return text.lstrip('0') or text


def _merge(into, extra):
    """Field by field, and a blank never replaces something.

    Only the consolidated sheet carries a manager column; the eleven per-entity
    sheets that follow it list the same people without one. Replacing whole
    records meant those wiped the reporting line the consolidated sheet had just
    supplied — 1,122 managers arrived and 2 survived.
    """
    for key, row in extra.items():
        current = into.setdefault(key, {'name': '', 'position': '', 'manager': ''})
        for field, value in row.items():
            if value:
                current[field] = value


def _ends(name):
    """First and last word, lowercased — enough to know two spellings of one
    person. "Sana Ghani" and "Sana Abdel Ghani" are the same employee; the
    particle is the only difference and it is not worth a mismatch."""
    parts = [w for w in re.split(r'\s+', (name or '').lower()) if w]
    return (parts[0], parts[-1]) if parts else ('', '')


def _pick(group, wanted):
    """Which of the people sharing a code the sheet is talking about."""
    if not wanted:
        return None
    exact = [e for e in group if e.full_name.lower() == wanted.lower()]
    if len(exact) == 1:
        return exact[0]
    near = [e for e in group if _ends(e.full_name) == _ends(wanted)]
    return near[0] if len(near) == 1 else None


class Command(BaseCommand):
    help = 'Import employee names, positions and direct managers from the HR spreadsheets.'

    def add_arguments(self, parser):
        parser.add_argument('files', nargs='+')
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change and write nothing.')

    def handle(self, *args, **options):
        try:
            import openpyxl
        except ImportError as exc:
            raise CommandError('openpyxl is needed to read the spreadsheets.') from exc

        rows = {}
        for name in options['files']:
            path = pathlib.Path(name)
            if not path.exists():
                raise CommandError(f'No such file: {path}')
            found = self._read(openpyxl, path)
            self.stdout.write(f'{path.name}: {len(found)} usable rows')
            _merge(rows, found)
        self.stdout.write(f'{len(rows)} people in the sheets\n')

        self._apply(rows, dry_run=options['dry_run'])

    def _read(self, openpyxl, path):
        """{code_key: {name, position, manager_code}} from every sheet that has
        the columns, so a file's layout does not have to be known in advance."""
        out = {}
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            for ws in wb.worksheets:
                header, at = None, 0
                for i, row in enumerate(ws.iter_rows(min_row=1, max_row=6, values_only=True)):
                    labels = [str(c).strip().lower() if c else '' for c in row]
                    if all(any(w == l for l in labels) for w in WANTED):
                        header, at = labels, i + 1
                        break
                if not header:
                    continue
                col = {label: n for n, label in enumerate(header) if label}
                ci, ni = col['employee code'], col['employee name']
                pi = col['position']
                mi = col.get('direct manager code')

                for row in ws.iter_rows(min_row=at + 1, values_only=True):
                    key = code_key(row[ci] if ci < len(row) else '')
                    if not key or not str(key).strip().isdigit():
                        continue
                    name = short_name(row[ni] if ni < len(row) else '')
                    if not name:
                        continue
                    _merge(out, {key: {
                        'name': name,
                        'position': (str(row[pi]).strip() if pi < len(row) and row[pi] else ''),
                        'manager': code_key(row[mi]) if (mi is not None and mi < len(row)) else '',
                    }})
        finally:
            wb.close()
        return out

    def _apply(self, rows, dry_run):
        # Employee codes are not unique here: 22 of them belong to two different
        # people each. Keyed naively, one of the pair silently answers for the
        # other and the import renames somebody into a colleague. Those codes are
        # left alone and reported instead.
        buckets = {}
        for emp in Employee.objects.all():
            buckets.setdefault(code_key(emp.emp_code), []).append(emp)

        people = {k: v[0] for k, v in buckets.items() if len(v) == 1}

        # A shared code is settled by name, not by giving somebody a new one.
        # For 21 of the 22 pairs one person is the one the sheet names and the
        # other is not in the export under any code — so there is no second code
        # to move them to, and inventing one would be making up HR data. The
        # named person imports; the other is left exactly as they are.
        shared, orphans = {}, []
        for key, group in buckets.items():
            if len(group) == 1:
                continue
            wanted = (rows.get(key) or {}).get('name', '')
            match = _pick(group, wanted)
            if match is not None:
                people[key] = match
                orphans += [e for e in group if e.pk != match.pk]
            else:
                shared[key] = group
        self.stdout.write(f'{sum(len(v) for v in buckets.values())} employees on file, '
                          f'{len(people)} with a code of their own')
        if orphans:
            self.stdout.write(self.style.WARNING(
                f'{len(orphans)} people share a code with somebody the sheet names, and '
                f'are not in the export under any code. Left untouched — HR should say '
                f'whether they have left:'))
            for e in sorted(orphans, key=lambda x: x.full_name)[:10]:
                self.stdout.write(f'    {code_key(e.emp_code):>6}  {e.full_name}')
        if shared:
            self.stdout.write(self.style.WARNING(
                f'{len(shared)} codes are shared and the sheet does not name either '
                f'person, so both are skipped:'))
            for k, v in sorted(shared.items())[:8]:
                self.stdout.write(f'    {k}: ' + ' / '.join(e.full_name for e in v))

        matched = [k for k in rows if k in people]
        self.stdout.write(f'{len(matched)} matched by employee code, '
                          f'{len(rows) - len(matched)} in the sheets with no record\n')

        renames, positions, managers, cleared, missing_mgr = [], 0, 0, 0, set()

        with transaction.atomic():
            for key in matched:
                row, emp = rows[key], people[key]
                changed = []

                if row['name'] and emp.full_name != row['name']:
                    renames.append((emp.full_name, row['name']))
                    emp.full_name = row['name']
                    changed.append('full_name')

                if row['position'] and emp.role != row['position']:
                    emp.role = row['position']
                    positions += 1
                    changed.append('role')

                mgr_key = row['manager']
                if mgr_key:
                    mgr = people.get(mgr_key)
                    if mgr is None:
                        missing_mgr.add(mgr_key)
                    elif mgr.pk == emp.pk:
                        pass                       # nobody manages themselves
                    elif emp.manager_id != mgr.pk:
                        emp.manager = mgr
                        managers += 1
                        changed.append('manager')

                if changed and not dry_run:
                    emp.save(update_fields=changed)

            # A name that is still ALL CAPS or carries a middle name means it was
            # not in the sheets at all — worth reporting rather than leaving.
            for key, emp in people.items():
                if key in rows:
                    continue
                tidy = short_name(emp.full_name)
                if tidy and tidy != emp.full_name:
                    renames.append((emp.full_name, tidy))
                    if not dry_run:
                        emp.full_name = tidy
                        emp.save(update_fields=['full_name'])
                    cleared += 1

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write('')
        self.stdout.write(f'names tidied     : {len(renames)}')
        self.stdout.write(f'  of those, not in the sheets: {cleared}')
        self.stdout.write(f'positions set    : {positions}')
        self.stdout.write(f'managers set     : {managers}')
        if missing_mgr:
            self.stdout.write(f'manager codes with no employee record: {len(missing_mgr)}'
                              f' e.g. {sorted(missing_mgr)[:6]}')
        self.stdout.write('')
        for was, now in renames[:12]:
            self.stdout.write(f'   {was[:38]:<40} -> {now}')
        if len(renames) > 12:
            self.stdout.write(f'   … and {len(renames) - 12} more')

        self.stdout.write('')
        self.stdout.write(self.style.WARNING('DRY RUN — nothing was written.') if dry_run
                          else self.style.SUCCESS('Written.'))
