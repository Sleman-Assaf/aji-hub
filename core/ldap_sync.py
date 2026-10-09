# core/ldap_sync.py
"""Import everyone from Active Directory into the Hub, and keep them in step.

Where ldap_auth.py answers "is this password right?" one person at a time,
this module walks the whole directory: every person under the Base DN becomes
a Hub account, names and emails follow AD, and an account disabled in AD is
disabled here on the next sync. Run it once for the initial import; run it on
a schedule and the Hub stops being a second place to administer people.

It needs the service (bind) account from the LDAP settings — enumeration is a
read of the directory, which anonymous binds are almost always refused. The
per-person UPN-bind login mode keeps working for sign-in either way.

Ground rules, chosen so a sync can never lock anyone out of their own system:
* Only accounts that LOOK AD-sourced are ever touched destructively — an
  account with a usable local password, or a superuser, is never deactivated
  by a sync, whatever AD says or doesn't.
* Nothing is ever deleted. Disabled means is_active=False: history, messages
  and files stay attached to the person.
* Removing people who vanished from AD is opt-in (--deactivate-missing),
  because "not returned by the search" is sometimes a moved OU, not a
  departure.

Run:  python manage.py sync_ad_users            (once)
      python manage.py sync_ad_users --loop 60  (keep syncing, hourly)
"""
import json
import logging

from django.contrib.auth.models import User
from django.utils import timezone

from .ldap_auth import get_config, is_configured, _server
from .models import GlobalSetting

logger = logging.getLogger(__name__)

KEY_LAST_SYNC = 'ldap_last_sync'

#: People, not machines or groups: computer accounts also have
#: sAMAccountName (ending in $), and objectCategory=person excludes them
#: along with contacts and service abstractions.
PERSON_FILTER = '(&(objectClass=user)(objectCategory=person)(sAMAccountName=*))'

#: Bit 2 of userAccountControl is ACCOUNTDISABLE — the flag the AD console's
#: "Disable Account" sets.
UF_ACCOUNTDISABLE = 0x2


def fetch_ad_users(config=None, connection=None):
    """Every person in the directory → (ok, message, [rows]).

    Rows: {username, first, last, email, enabled}. Paged search, because AD
    caps a plain search at 1000 entries and an office of 1,250 people would
    silently lose the tail. `connection` is injectable for tests.
    """
    config = config or get_config()
    if not is_configured(config):
        return False, 'LDAP is not configured (host and Base DN required).', []
    if not config['bind_dn']:
        return False, ('Importing needs the service (bind) account — set '
                       'Bind DN and its password in the LDAP settings. '
                       'Per-user login keeps working without it.'), []
    try:
        from ldap3 import Connection, SUBTREE
    except ImportError:
        return False, "The 'ldap3' package is not installed on the server.", []

    attrs = ['sAMAccountName', config['attr_first'], config['attr_last'],
             config['attr_email'], 'userAccountControl',
             # The whole person, as the AD console's tabs know them. Anything
             # a directory does not populate simply arrives empty and is left
             # alone on the Hub side — the sync never blanks a filled field.
             'displayName',                   # full name as HR wrote it
             'title',                         # job title (Organization tab)
             'department', 'company',         # org structure
             'manager',                       # the manager — as a DN
             'ipPhone',                       # the usual home of the extension
             'telephoneNumber', 'mobile',     # phones
             'physicalDeliveryOfficeName',    # office / site
             'co',                            # country
             'employeeID', 'employeeNumber',  # HR identifiers
             'thumbnailPhoto']                # profile photo, raw JPEG
    try:
        conn = connection or Connection(
            _server(config), user=config['bind_dn'],
            password=config['bind_password'], receive_timeout=20)
        if not conn.bound and not conn.bind():
            return False, ('Service account bind failed: '
                           f'{conn.result.get("description", "")}'), []

        rows, seen = [], set()

        def _take(attributes):
            def _text(name):
                v = attributes.get(name)
                # ldap3 hands attributes back as scalars or single-item
                # lists depending on the server's schema knowledge; both
                # spellings mean the same value.
                if isinstance(v, (list, tuple)):
                    v = v[0] if v else ''
                return str(v if v is not None else '').strip()

            uname = _text('sAMAccountName')
            if not uname or uname.endswith('$'):
                return
            key = uname.lower()
            if key in seen:
                return
            seen.add(key)
            try:
                uac = int(_text('userAccountControl') or 0)
            except (TypeError, ValueError):
                uac = 0

            def _bytes(name):
                v = attributes.get(name)
                if isinstance(v, (list, tuple)):
                    v = v[0] if v else None
                return v if isinstance(v, (bytes, bytearray)) else None

            rows.append({
                'username': key,
                'first': _text(config['attr_first']),
                'last': _text(config['attr_last']),
                'email': _text(config['attr_email']),
                'enabled': not (uac & UF_ACCOUNTDISABLE),
                'dn': str(attributes.get('_dn') or '').strip(),
                'profile': {
                    'full_name': _text('displayName'),
                    'role': _text('title'),
                    'department': _text('department'),
                    'company': _text('company'),
                    'manager_dn': _text('manager'),
                    'extension': _text('ipPhone') or _text('telephoneNumber'),
                    'mobile': _text('mobile'),
                    'office': _text('physicalDeliveryOfficeName'),
                    'country': _text('co'),
                    'emp_code': _text('employeeID') or _text('employeeNumber'),
                },
                'photo': _bytes('thumbnailPhoto'),
            })

        try:
            # The right way: server-side paging, 500 at a time.
            for entry in conn.extend.standard.paged_search(
                    config['base_dn'], PERSON_FILTER, search_scope=SUBTREE,
                    attributes=attrs, paged_size=500, generator=True):
                if entry.get('type') == 'searchResEntry':
                    found = dict(entry.get('attributes') or {})
                    found['_dn'] = entry.get('dn') or ''
                    _take(found)
        except Exception:
            # Some servers (and the offline mock used in tests) don't do the
            # paging control; a plain search still covers directories under
            # the 1000-entry cap.
            if conn.search(config['base_dn'], PERSON_FILTER,
                           search_scope=SUBTREE, attributes=attrs):
                for entry in conn.entries:
                    found = {a: entry[a].value if a in entry else ''
                             for a in attrs}
                    found['_dn'] = entry.entry_dn
                    _take(found)
        if connection is None:
            conn.unbind()
        return True, f'{len(rows)} people read from the directory.', rows
    except Exception as exc:
        logger.error('[ldap-sync] fetch failed: %s', exc)
        return False, f'Directory read failed: {exc}', []


def _split_mobile(raw):
    """'+962 79 012 3456' → ('962', '790123456'); a bare local number stays
    local. AD stores mobiles however HR typed them, so this is a tidy-up, not
    a validation: digits kept, and when the number plainly carries a country
    code (a leading + or 00), the last nine digits are the phone and the rest
    the code — the pattern of every AJi country's numbering."""
    digits = ''.join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return '', ''
    had_prefix = str(raw).strip().startswith('+') or digits.startswith('00')
    if digits.startswith('00'):
        digits = digits[2:]
    if had_prefix and len(digits) > 9:
        return digits[:-9], digits[-9:]
    return '', digits


def _sync_profile(user, row, dry_run):
    """One person's AD record onto their People profile.

    Finds the Employee the HR imports already created — by linked user, then
    employee code, then email, then exact name — and links rather than
    duplicates. AD fills what it knows; a field AD leaves empty is never used
    to blank what HR entered. Returns (employee | None, changed: bool).
    """
    from people.models import Department, Employee, Entity

    p = row['profile']
    emp = getattr(user, 'employee', None)
    if emp is None:
        candidates = Employee.objects.filter(user__isnull=True)
        emp = (candidates.filter(emp_code=p['emp_code']).first()
               if p['emp_code'] else None)
        if emp is None and row['email']:
            emp = candidates.filter(email__iexact=row['email']).first()
        if emp is None and p['full_name']:
            emp = candidates.filter(full_name__iexact=p['full_name']).first()
    created = False
    if emp is None:
        emp = Employee(full_name=p['full_name']
                       or f"{row['first']} {row['last']}".strip()
                       or row['username'])
        created = True

    dirty = []

    def _set(field, value):
        if value and getattr(emp, field) != value:
            setattr(emp, field, value)
            dirty.append(field)

    if emp.user_id != user.pk:
        emp.user = user
        dirty.append('user')
    # AD gave no mail attribute? The address is derivable on the spot: the
    # login name with _ for . at the company domain — the same address the
    # HR table carries as its unique index, which is what the HR feed joins
    # on right after this sync.
    if not row['email'] and '.' in user.username:
        from people.hr_feed import _mail_domain
        derived = (user.username.strip().lower().replace('.', '_')
                   + '@' + _mail_domain())
        row = dict(row, email=derived)
        if not user.email:
            user.email = derived
            user.save(update_fields=['email'])
    _set('full_name', p['full_name'])
    _set('role', p['role'])
    _set('email', row['email'])
    _set('extension', p['extension'][:20])
    _set('office', p['office'][:100])
    _set('country', p['country'][:100])
    _set('emp_code', p['emp_code'][:30])
    cc, num = _split_mobile(p['mobile'])
    _set('mobile_country_code', cc[:5])
    _set('mobile_number', num[:20])
    if p['company'] and (emp.entity is None
                         or p['company'] not in (emp.entity.name,
                                                 emp.entity.short_name)):
        if not dry_run:
            emp.entity = (Entity.objects.filter(name=p['company']).first()
                          or Entity.objects.create(
                              name=p['company'],
                              code=p['company'][:30].upper().replace(' ', '-')))
        dirty.append('entity')
    if p['department'] and (emp.department is None
                            or emp.department.name != p['department']):
        # Department names repeat across entities; a bare get_or_create(name=)
        # raises MultipleObjectsReturned on real data. Prefer the one under
        # this person's entity, else the oldest, else create.
        if not dry_run:
            from people.hr_feed import _department
            emp.department = _department(p['department'], emp.entity)
        dirty.append('department')

    if (created or dirty) and not dry_run:
        emp.save()
    # The photo, only into an empty frame: people who uploaded their own
    # picture on the Hub keep it — AD's thumbnail is a fallback, not an
    # authority.
    if row.get('photo') and not dry_run and not emp.photo:
        from django.core.files.base import ContentFile
        emp.photo.save(f"{row['username']}.jpg",
                       ContentFile(row['photo']), save=True)
        dirty.append('photo')
    return emp, created or bool(dirty)


def _ad_sourced(user):
    """An account this sync may govern: born from AD (no usable local
    password) and not a superuser. Everything else belongs to humans."""
    return not user.has_usable_password() and not user.is_superuser


def sync_users(dry_run=False, deactivate_missing=False, config=None,
               connection=None, ran_by=''):
    """One full pass: AD is read, the Hub is brought level, a report returned.

    The report is also stored (unless dry-run) so the settings page can show
    when the directory and the Hub last agreed.
    """
    ok, message, rows = fetch_ad_users(config, connection=connection)
    if not ok:
        return {'ok': False, 'message': message}

    created, updated, deactivated, reactivated = [], [], [], []
    unchanged = 0
    profiles_changed = 0
    by_dn, manager_pending = {}, []
    ad_usernames = {r['username'] for r in rows}

    for row in rows:
        try:
            user = User.objects.get(username__iexact=row['username'])
        except User.DoesNotExist:
            if dry_run:
                created.append(row['username'])
                continue
            user = User(username=row['username'],
                        first_name=row['first'][:150],
                        last_name=row['last'][:150],
                        email=row['email'][:254],
                        is_active=row['enabled'])
            user.set_unusable_password()
            user.save()
            created.append(row['username'])
            emp, _changed = _sync_profile(user, row, dry_run)
            if emp is not None:
                if row['dn']:
                    by_dn[row['dn'].lower()] = emp
                if row['profile']['manager_dn']:
                    manager_pending.append((emp, row['profile']['manager_dn']))
            continue
        except User.MultipleObjectsReturned:
            logger.warning('[ldap-sync] ambiguous username %r skipped',
                           row['username'])
            continue

        dirty = []
        if row['first'] and user.first_name != row['first'][:150]:
            user.first_name = row['first'][:150]; dirty.append('first_name')
        if row['last'] and user.last_name != row['last'][:150]:
            user.last_name = row['last'][:150]; dirty.append('last_name')
        if row['email'] and user.email != row['email'][:254]:
            user.email = row['email'][:254]; dirty.append('email')
        # The active flag follows AD — but only on accounts this sync owns.
        if _ad_sourced(user) and user.is_active != row['enabled']:
            user.is_active = row['enabled']; dirty.append('is_active')
            (reactivated if row['enabled'] else deactivated).append(
                row['username'])
        emp, profile_dirty = _sync_profile(user, row, dry_run)
        if profile_dirty:
            profiles_changed += 1
        if emp is not None:
            if row['dn']:
                by_dn[row['dn'].lower()] = emp
            if row['profile']['manager_dn']:
                manager_pending.append((emp, row['profile']['manager_dn']))
        if dirty:
            if not dry_run:
                user.save(update_fields=dirty)
            if 'is_active' not in dirty:
                updated.append(row['username'])
        elif profile_dirty:
            updated.append(row['username'])
        else:
            unchanged += 1

    if deactivate_missing:
        gone = (User.objects
                .filter(is_active=True)
                .exclude(username__in=ad_usernames))
        for user in gone:
            if not _ad_sourced(user):
                continue
            if not dry_run:
                user.is_active = False
                user.save(update_fields=['is_active'])
            deactivated.append(user.username)

    # Managers last: AD names each person's boss by DN, which only resolves
    # once everybody has been walked.
    managers_linked = 0
    for emp, manager_dn in manager_pending:
        boss = by_dn.get(manager_dn.lower())
        if boss is None or boss.pk == emp.pk or emp.manager_id == boss.pk:
            continue
        if not dry_run:
            emp.manager = boss
            emp.save(update_fields=['manager'])
        managers_linked += 1

    report = {
        'ok': True,
        'ran_at': timezone.now().isoformat(),
        'ran_by': ran_by,
        'dry_run': dry_run,
        'directory_total': len(rows),
        'created': len(created),
        'updated': len(updated),
        'deactivated': len(deactivated),
        'reactivated': len(reactivated),
        'unchanged': unchanged,
        'profiles_changed': profiles_changed,
        'managers_linked': managers_linked,
        'names': {'created': created[:50], 'deactivated': deactivated[:50]},
        'message': (f'{len(rows)} in AD: {len(created)} created, '
                    f'{len(updated)} updated, {len(reactivated)} reactivated, '
                    f'{len(deactivated)} deactivated, {unchanged} unchanged; '
                    f'{profiles_changed} profiles updated, '
                    f'{managers_linked} managers linked'
                    + (' (dry run — nothing written)' if dry_run else '')),
    }
    # HR fills whatever gaps AD left, names follow HR spelling — the same
    # rules whichever system spoke last. Best-effort: an HR-side hiccup must
    # not fail an AD sync.
    if not dry_run:
        try:
            from people.hr_feed import apply_hr
            hr = apply_hr()
            report['hr_feed'] = hr['message']
            report['message'] += f" | HR feed: {hr['message']}"
        except Exception as exc:
            logger.warning('[ldap-sync] HR feed skipped: %s', exc)

    if not dry_run:
        GlobalSetting.set(KEY_LAST_SYNC, json.dumps(report))
    logger.info('[ldap-sync] %s', report['message'])
    return report


def last_sync():
    raw = GlobalSetting.get(KEY_LAST_SYNC, '')
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None
