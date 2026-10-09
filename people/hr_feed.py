# people/hr_feed.py
"""Settle every Employee field between its two sources: AD first, HR for gaps.

Two systems describe the same people. Active Directory is live — IT disables
an account the day someone leaves — so where AD supplied a value, that value
stands. The HR sheet is authoritative for what HR owns and AD often lacks:
departments, positions, birth dates, the reporting line. So the feed fills
only fields that are still empty — with one deliberate exception: the name is
ALWAYS HR's, because HR spells names the way the contract does, where AD
holds whatever IT typed when the account was made.

Matching, strongest key first:
    1. email        — the unique index, once the HR sheet carries one
    2. emp_code     — what AD's employeeID and the sheet's Employee Code share
    3. exact name   — last resort, case-insensitive, skipped when ambiguous

Run standalone (manage.py import_hr_excel triggers it after every import),
and automatically at the end of every AD sync, so the order of operations
never matters: whichever system spoke last, the rules above decide.
"""
import logging

from django.db.models import Q

logger = logging.getLogger(__name__)


def _department(name, entity=None):
    """One Department for this name — never a crash on duplicates.

    Department names are not unique (each entity may carry its own "Clients
    and Markets"), so: the one under the employee's entity if it exists, else
    the oldest of that name, else a fresh one attached to the entity.
    """
    from .models import Department
    qs = Department.objects.filter(name=name).order_by('id')
    if entity is not None:
        scoped = qs.filter(entity=entity).first()
        if scoped is not None:
            return scoped
    found = qs.first()
    if found is not None:
        return found
    return Department.objects.create(name=name, entity=entity)


def _mail_domain():
    from django.conf import settings
    return getattr(settings, 'AJI_MAIL_DOMAIN', 'ajigroup.com')


def _fix_domain(email):
    """A legacy domain spelling (@aj-group.com, @ajgroup.com) rewritten to
    the real one; anything else — other companies, personal addresses —
    passes through untouched. Returns the (possibly unchanged) address."""
    from django.conf import settings
    aliases = getattr(settings, 'AJI_MAIL_DOMAIN_ALIASES',
                      ['aj-group.com', 'ajgroup.com'])
    email = (email or '').strip()
    if '@' not in email:
        return email
    local, _, domain = email.rpartition('@')
    if domain.lower() in aliases:
        return f'{local}@{_mail_domain()}'
    return email


def _name_keys(full_name):
    """The username shapes a name can take: first.last and f.last.

    AJi logins are built from names — Abdallah.Takrouri, n.alzoubi — and the
    HR sheet holds the full legal name those pieces came from. First and last
    alphabetic words give the two candidate shapes; punctuation-only tokens
    (HR writes "Munzer . Haddadin") are ignored.
    """
    words = [w for w in str(full_name).split() if any(c.isalpha() for c in w)]
    if len(words) < 2:
        return set()
    first, last = words[0].lower(), words[-1].lower()
    return {f'{first}.{last}', f'{first[0]}.{last}'}


def _match(hr, by_email, by_code, by_name, by_key=None):
    if hr.email:
        emp = by_email.get(hr.email.lower())
        if emp is not None:
            return emp
    if hr.emp_code:
        candidates = by_code.get(hr.emp_code.strip())
        if candidates and len(candidates) == 1:
            return candidates[0]
        if candidates:
            # The same code exists in two entities; the name decides.
            named = [e for e in candidates
                     if e.full_name.strip().lower() == hr.full_name.strip().lower()]
            if len(named) == 1:
                return named[0]
    names = by_name.get(hr.full_name.strip().lower())
    if names and len(names) == 1:
        return names[0]
    # Last resort: the login-name shape. An Employee born from a bare AD
    # account has neither email, code, nor a real name — only a username like
    # abdallah.takrouri — and that is derivable from the HR name. Only an
    # unambiguous hit counts, on both sides: a key two HR rows share, or two
    # employees share, proves nothing.
    if by_key:
        hits = set()
        for key in _name_keys(hr.full_name):
            hits.update(by_key.get(key) or [])
        if len(hits) == 1:
            return next(iter(hits))
    return None


def apply_hr(dry_run=False, create_missing=False):
    """One pass: every HR row onto its Employee. Returns a report dict."""
    from .models import EmpHR, Employee

    employees = list(Employee.objects.select_related('department', 'entity',
                                                     'user'))

    # Emails first, matching second: the mailbox is derivable from any
    # name-shaped login (dot becomes underscore at the mail domain), and once
    # both sides carry it, email is the unique index that links an Employee
    # to their HR row — the strongest key, tried first. Real addresses from
    # AD or typed by hand always stand.
    # Correction sweep first: legacy domain spellings — the dash form
    # @aj-group.com and the no-i form @ajgroup.com — become the real
    # @ajigroup.com wherever they sit: HR rows, employees, login accounts.
    emails_corrected = 0
    for hr in EmpHR.objects.exclude(email=None).exclude(email=''):
        fixed = _fix_domain(hr.email)
        if fixed != hr.email:
            # The unique column: if the corrected address already exists on
            # another row, this row loses its email rather than crashing.
            if EmpHR.objects.filter(email=fixed).exclude(pk=hr.pk).exists():
                fixed = None
            if not dry_run:
                hr.email = fixed
                hr.save(update_fields=['email'])
            emails_corrected += 1
    for e in employees:
        fixed = _fix_domain(e.email)
        if e.email and fixed != e.email:
            e.email = fixed
            emails_corrected += 1
            if not dry_run:
                e.save(update_fields=['email'])
        user = getattr(e, 'user', None)
        if user is not None and user.email:
            fixed = _fix_domain(user.email)
            if fixed != user.email:
                user.email = fixed
                emails_corrected += 1
                if not dry_run:
                    user.save(update_fields=['email'])

    emails_derived = 0
    for e in employees:
        user = getattr(e, 'user', None)
        if user is None or '.' not in user.username:
            continue
        derived = (user.username.strip().lower().replace('.', '_')
                   + '@' + _mail_domain())
        changed = False
        if not e.email:
            e.email = derived
            changed = True
            if not dry_run:
                e.save(update_fields=['email'])
        if not user.email:
            user.email = derived
            changed = True
            if not dry_run:
                user.save(update_fields=['email'])
        if changed:
            emails_derived += 1

    by_email, by_code, by_name, by_key = {}, {}, {}, {}
    hr_key_counts = {}
    for hr in EmpHR.objects.all():
        for key in _name_keys(hr.full_name):
            hr_key_counts[key] = hr_key_counts.get(key, 0) + 1
    for e in employees:
        if e.email:
            by_email.setdefault(e.email.lower(), e)
        if e.emp_code:
            by_code.setdefault(e.emp_code.strip(), []).append(e)
        by_name.setdefault(e.full_name.strip().lower(), []).append(e)
        # Username shapes this employee answers to: the login itself, and a
        # dotted full_name (bare AD accounts carry the username there too).
        keys = set()
        if e.user_id and e.user and e.user.username:
            keys.add(e.user.username.strip().lower())
        if '.' in e.full_name and ' ' not in e.full_name.strip():
            keys.add(e.full_name.strip().lower())
        for key in keys:
            # A key that two HR rows would claim identifies nobody.
            if hr_key_counts.get(key, 0) <= 1:
                by_key.setdefault(key, set()).add(e)

    matched, renamed, filled, unmatched = 0, 0, 0, []
    created_employees = 0
    duplicates = 0
    claimed = set()          # employees already taken by an earlier HR row
    manager_later = []          # (employee, manager's own EmpHR row)

    for hr in EmpHR.objects.all():
        emp = _match(hr, by_email, by_code, by_name, by_key)
        if emp is not None and id(emp) in claimed:
            # A second HR row for the same person (dual-entity staff): the
            # first row — the sheet is walked in a fixed order — owns the
            # record; later twins are noted, not applied, so passes never
            # fight over the name.
            duplicates += 1
            continue
        if emp is None and create_missing:
            # No Employee anywhere — usually site staff with no AD account.
            # The directory should still know them: a login-less Employee,
            # born whole from the HR row. If AD ever creates their account,
            # the email index links it to this record on the next sync.
            emp = Employee(full_name=hr.full_name,
                           email=hr.email or '',
                           emp_code=hr.emp_code,
                           role=hr.position[:200],
                           entity=hr.entity,
                           department=hr.department,
                           section=hr.section,
                           country=hr.country[:100],
                           office=hr.site_project[:100] or hr.location[:100],
                           birthday=(hr.birth_date.strftime('%m-%d')
                                     if hr.birth_date else ''))
            if not dry_run:
                emp.save()
            employees.append(emp)
            if emp.email:
                by_email.setdefault(emp.email.lower(), emp)
            if emp.emp_code:
                by_code.setdefault(emp.emp_code.strip(), []).append(emp)
            by_name.setdefault(emp.full_name.strip().lower(), []).append(emp)
            created_employees += 1
            claimed.add(id(emp))
            matched += 1
            if hr.manager_id:
                manager_later.append((emp, hr.manager))
            continue
        if emp is None:
            unmatched.append(f'{hr.full_name} ({hr.entity.code if hr.entity_id else "?"} {hr.emp_code})')
            continue
        claimed.add(id(emp))
        matched += 1
        dirty = []

        # The one field HR always wins.
        if hr.full_name and emp.full_name != hr.full_name:
            emp.full_name = hr.full_name
            dirty.append('full_name')
            renamed += 1

        # Everything else: only into the gaps AD (or anyone) left.
        def _fill(field, value):
            if value and not getattr(emp, field):
                setattr(emp, field, value)
                dirty.append(field)

        _fill('role', hr.position[:200])
        _fill('emp_code', hr.emp_code[:30])
        _fill('office', hr.site_project[:100] or hr.location[:100])
        if hr.birth_date and not emp.birthday:
            emp.birthday = hr.birth_date.strftime('%m-%d')
            dirty.append('birthday')
        # The HR row already points at the org graph — the same Entity,
        # Department and Section rows Employee uses — so filling is plain
        # assignment, no name matching at feed time.
        if hr.entity_id and emp.entity_id is None:
            emp.entity = hr.entity
            dirty.append('entity')
        if hr.department_id and emp.department_id is None:
            emp.department = hr.department
            dirty.append('department')
        if hr.section_id and emp.section_id is None:
            emp.section = hr.section
            dirty.append('section')
        _fill('country', hr.country[:100])
        if hr.manager_id and emp.manager is None:
            manager_later.append((emp, hr.manager))

        if dirty:
            filled += 1
            if not dry_run:
                emp.save()

        # The login account's own name fields are AD's to write — but when AD
        # left them empty (a directory with bare accounts), the greeting and
        # every "from <name>" line fall back to the username. HR's spelling
        # fills the void: first word as the first name, the rest as the last.
        user = getattr(emp, 'user', None)
        user_dirty = []
        if (user is not None and hr.full_name
                and not (user.first_name or user.last_name)):
            parts = hr.full_name.split()
            user.first_name = parts[0][:150]
            user.last_name = ' '.join(parts[1:])[:150]
            user_dirty += ['first_name', 'last_name']

        if user_dirty:
            if not dry_run:
                user.save(update_fields=user_dirty)
            filled += 1

    # Managers last, once everyone has been walked: the HR row already links
    # to the manager's own HR row; that row is matched to its Employee the
    # same way any row is.
    managers_linked = 0
    for emp, boss_hr in manager_later:
        boss = _match(boss_hr, by_email, by_code, by_name, by_key)
        if boss is None or boss.pk == emp.pk or emp.manager_id == boss.pk:
            continue
        if not dry_run:
            emp.manager = boss
            emp.save(update_fields=['manager'])
        managers_linked += 1

    report = {
        'ok': True,
        'hr_rows': EmpHR.objects.count(),
        'matched': matched,
        'renamed': renamed,
        'fields_filled_on': filled,
        'managers_linked': managers_linked,
        'employees_created': created_employees,
        'duplicate_hr_rows': duplicates,
        'emails_derived': emails_derived,
        'emails_corrected': emails_corrected,
        'unmatched': len(unmatched),
        'unmatched_names': unmatched[:30],
        'message': (f'{matched} matched of {EmpHR.objects.count()} HR rows: '
                    f'{renamed} renamed to the HR spelling, {filled} had gaps '
                    f'filled, {managers_linked} managers linked, '
                    f'{len(unmatched)} unmatched'
                    + (' (dry run — nothing written)' if dry_run else '')),
    }
    logger.info('[hr-feed] %s', report['message'])
    return report
