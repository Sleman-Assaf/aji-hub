# core/it_views.py
"""The IT portal.

One place for the work that used to need the Django back-office and the single
superuser account: who holds which role, who is on the system, backups, the
audit trail, and a security read-out.

Everything here is gated on `user_can_manage_settings` — IT, Intranet Admin, or
a superuser — the same rule Global Settings uses, so the two cannot disagree
about who is an administrator.

Every action that changes something writes an ITAuditLog row. That is the point
of the portal as much as the screens are: the Hub previously had no record of
who granted a role or disabled an account.
"""
import os
import shutil
import sys
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlencode

from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import (BackupRecord, GlobalSetting, INTRANET_ADMIN_GROUP, IT_GROUP,
                     ITAuditLog, ROLE_CAPABILITIES, backup_dir,
                     is_intranet_admin, user_can_manage_settings)


def it_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not user_can_manage_settings(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


def _tell(request, text, level='success'):
    from django.contrib import messages as dj
    getattr(dj, level)(request, text)


#: Roles the portal will not let you empty or delete from here. Locking yourself
#: out of the portal is a mistake with no in-Hub way back.
PROTECTED_ROLES = {INTRANET_ADMIN_GROUP, IT_GROUP}


# ── overview ────────────────────────────────────────────────────────────────

@it_required
def it_home(request):
    from . import ldap_auth, msgraph, whatsapp
    from .mailer import transport_status

    users = User.objects.all()
    roles = Group.objects.annotate(n=Count('user')).order_by('name')
    last_backup = BackupRecord.objects.first()

    try:
        mail_ok, mail_note = transport_status()
    except Exception:
        mail_ok, mail_note = False, 'Could not read the mail settings.'

    integrations = [
        ('Email', mail_ok, mail_note),
        ('Calendar (Outlook)', msgraph.calendar_enabled() if hasattr(msgraph, 'calendar_enabled')
         else bool(GlobalSetting.get(msgraph.KEY_CAL_ENABLED) == '1'),
         'Outlook calendar sync'),
        ('Sign-in (LDAP)', ldap_auth.get_config()['enabled'] and ldap_auth.is_configured(),
         'Active Directory sign-in'),
        ('WhatsApp', whatsapp.is_configured(), 'Outbound WhatsApp messages'),
    ]

    return render(request, 'core/it/home.html', {
        'tab': 'overview',
        'n_users': users.count(),
        'n_active': users.filter(is_active=True).count(),
        'n_disabled': users.filter(is_active=False).count(),
        'n_never': users.filter(last_login__isnull=True).count(),
        'n_roles': roles.count(),
        'n_privileged': User.objects.filter(
            Q(is_superuser=True) | Q(groups__name__in=PROTECTED_ROLES)).distinct().count(),
        'roles': roles,
        'integrations': integrations,
        'last_backup': last_backup,
        'recent': ITAuditLog.objects.select_related('actor')[:8],
        'security_count': len(_security_findings()),
    })


# ── roles and access ────────────────────────────────────────────────────────

#: What somebody with no role at all can already do. Not a role and not
#: editable: it is what being signed in means, and it is here because the first
#: question anybody asks of a roles screen is "what do they get without one?"
STANDARD_ACCESS = [
    ('Homepage', 'News and whatever has been published'),
    ('People & Culture', 'HR announcements, wellness, childcare — reading only'),
    ('Onboarding', 'The CEO letter and the employee handbook'),
    ('Directory', 'Colleagues, extensions, skills and the org chart, in full'),
    ('AJi Exchange', 'Read public communities; post in the ones they join'),
    ('SOPs & Manuals', 'Every library that is open to everyone'),
    ('Brand Files', 'View and download whatever Branding has left public'),
    ('AJi Assist', 'Ask anything, and export the conversation'),
    ('Messages', 'Chat with any colleague, one to one or in groups'),
    ('Their own account', 'Profile, notification switches, and their own photo and skills'),
]


@it_required
def it_roles(request):
    roles = []
    for g in Group.objects.annotate(n=Count('user')).order_by('name'):
        roles.append({
            'group': g,
            'count': g.n,
            'capabilities': ROLE_CAPABILITIES.get(g.name, []),
            'protected': g.name in PROTECTED_ROLES,
            'undocumented': g.name not in ROLE_CAPABILITIES,
        })
    return render(request, 'core/it/roles.html', {
        'tab': 'roles',
        'roles': roles,
        'standard': STANDARD_ACCESS,
        'n_standard': User.objects.filter(is_active=True, groups__isnull=True).count(),
    })


@it_required
@require_POST
def it_role_delete(request, pk):
    """Remove a role entirely.

    Two things stop it. A role the code looks up by name cannot go, because the
    screens behind it would simply stop letting anyone in. And a role somebody
    still holds is emptied first on purpose: deleting one silently strips it
    from every member, and that should be a decision rather than a side effect.
    """
    group = get_object_or_404(Group, pk=pk)
    if group.name in PROTECTED_ROLES:
        _tell(request, f'{group.name} is checked for by name in the code. '
                       f'Deleting it would switch off the screens behind it.', 'error')
        return redirect('it_role_detail', pk=pk)

    holders = group.user_set.count()
    if holders and request.POST.get('confirm_members') != 'yes':
        _tell(request, f'{holders} {"person" if holders == 1 else "people"} still hold '
                       f'{group.name}. Remove them first, or confirm to take it '
                       f'from all of them.', 'error')
        return redirect('it_role_detail', pk=pk)

    name = group.name
    known = name in ROLE_CAPABILITIES
    group.delete()
    ITAuditLog.write(request, ITAuditLog.Action.ROLE_DELETE,
                     f'Role “{name}” deleted'
                     + (f', taken from {holders} member(s)' if holders else ''),
                     target=name)
    if known:
        _tell(request, f'“{name}” is gone. It was a role the Hub knows by name, so '
                       f'the screens that used it now let nobody in — recreate it with '
                       f'exactly that name to restore them.', 'warning')
    else:
        _tell(request, f'“{name}” deleted.')
    return redirect('it_roles')


#: Django's own plumbing. Not shown at all.
#:
#: These were listed, folded away at the bottom, on the reasoning that hiding
#: something is worse than showing it. That was wrong here. "Can add log entry",
#: "can delete session", "can add permission" are Django's internals, not
#: anything an administrator of this Hub decides — and `auth` in particular
#: hands out the ability to edit users and roles from the back-office, which is
#: precisely what this portal exists to replace. Nothing in the roles screen
#: should be Django's business.
#:
#: Permissions already granted on these apps are left alone rather than revoked:
#: they are not shown, so a save from this page cannot clear them either. See
#: it_role_permissions, which unions rather than overwrites.
HIDDEN_APPS = {'admin', 'contenttypes', 'sessions', 'auth'}

#: What each app is, said as the Hub says it rather than as Django names it.
APP_LABELS = {
    'portal': 'Policies, Brand Files and Onboarding',
    'content': 'News, calendar and portfolio',
    'people': 'Employee directory',
    'cms': 'Pages and navigation',
    'exchange': 'AJi Exchange',
    'messaging': 'Messages',
    'services': 'Service requests',
    'cafeteria': 'Cafeteria',
    'training': 'Training',
    'dam': 'Documents library',
    'assist': 'AJi Assist',
    'voice': 'Employee voice',
    'core': 'Notifications and audit',
}

ACTIONS = ('add', 'change', 'delete', 'view')


def _permission_tree(group):
    """Permissions grouped app → model → the four actions.

    393 of them in a flat list is not a thing anybody can read. Grouped this
    way it is the same shape as the question being asked: "what may this role
    do to policies?"
    """
    from django.contrib.auth.models import Permission
    held = set(group.permissions.values_list('pk', flat=True)) if group else set()
    perms = (Permission.objects.select_related('content_type')
             .exclude(content_type__app_label__in=HIDDEN_APPS)
             .order_by('content_type__app_label', 'content_type__model', 'codename'))

    apps = {}
    for p in perms:
        app = p.content_type.app_label
        model = p.content_type.model
        entry = apps.setdefault(app, {})
        row = entry.setdefault(model, {'model': model,
                                       'label': p.content_type.name.title(),
                                       'actions': {}, 'other': []})
        action = p.codename.split('_', 1)[0]
        cell = {'pk': p.pk, 'on': p.pk in held, 'name': p.name}
        if action in ACTIONS and p.codename == f'{action}_{model}':
            row['actions'][action] = cell
        else:
            # Custom permissions such as cms.publish_page, which do not fit the
            # add/change/delete/view grid and would vanish if only it was drawn.
            row['other'].append({**cell, 'codename': p.codename})

    out = []
    for app in sorted(apps, key=lambda a: APP_LABELS.get(a, a)):
        rows = [apps[app][m] for m in sorted(apps[app])]
        n_on = sum(1 for r in rows for c in list(r['actions'].values()) + r['other'] if c['on'])
        out.append({
            'app': app,
            'label': APP_LABELS.get(app, app.title()),
            'rows': rows,
            'n_on': n_on,
        })
    return out


@it_required
def it_role_detail(request, pk):
    group = get_object_or_404(Group, pk=pk)
    return render(request, 'core/it/role_detail.html', {
        'tab': 'roles',
        'group': group,
        'members': group.user_set.select_related('employee').order_by(
            'first_name', 'last_name', 'username'),
        'capabilities': ROLE_CAPABILITIES.get(group.name, []),
        'protected': group.name in PROTECTED_ROLES,
        'tree': _permission_tree(group),
        'n_perms': group.permissions.count(),
        'actions': ACTIONS,
    })


@it_required
@require_POST
def it_role_permissions(request, pk):
    """What this role may do, in Django's own permission terms.

    The same thing the Django admin's group screen edits, which is where this
    had to be done before. Note these are separate from what the Hub's own
    screens check: most of those ask "is this person in the group", not "does
    this person hold this permission". Both matter, and the page says so.
    """
    group = get_object_or_404(Group, pk=pk)
    chosen = {int(v) for v in request.POST.getlist('perms') if str(v).isdigit()}
    held = set(group.permissions.values_list('pk', flat=True))

    # Django's own apps are not drawn on this page, so they are not in the post
    # either. Setting the group to exactly what came back would silently revoke
    # every one of them — including, on Intranet Admin, the ability to use the
    # back-office at all. Anything hidden that the role already had is carried
    # through untouched.
    hidden = set(group.permissions.filter(
        content_type__app_label__in=HIDDEN_APPS).values_list('pk', flat=True))
    chosen |= hidden

    if chosen == held:
        _tell(request, 'No change.', 'info')
        return redirect(reverse('it_role_detail', args=[pk]) + '#permissions')

    group.permissions.set(chosen)
    added, removed = len(chosen - held), len(held - chosen)
    bits = []
    if added:
        bits.append(f'{added} added')
    if removed:
        bits.append(f'{removed} removed')
    ITAuditLog.write(request, ITAuditLog.Action.ROLE_EDIT,
                     f'{group.name} permissions: {", ".join(bits)} '
                     f'({len(chosen)} in total)', target=group.name)
    _tell(request, f'{group.name}: {" and ".join(bits)}.')
    return redirect(reverse('it_role_detail', args=[pk]) + '#permissions')


@it_required
@require_POST
def it_role_rename(request, pk):
    group = get_object_or_404(Group, pk=pk)
    name = (request.POST.get('name') or '').strip()[:80]
    if not name:
        _tell(request, 'Give the role a name.', 'error')
    elif name == group.name:
        pass
    elif Group.objects.filter(name__iexact=name).exclude(pk=pk).exists():
        _tell(request, f'A role called “{name}” already exists.', 'error')
    elif group.name in PROTECTED_ROLES:
        _tell(request, f'{group.name} is checked for by name in the code. '
                       f'Renaming it would switch it off.', 'error')
    else:
        was = group.name
        group.name = name
        group.save(update_fields=['name'])
        ITAuditLog.write(request, ITAuditLog.Action.ROLE_EDIT,
                         f'Role “{was}” renamed to “{name}”', target=name)
        _tell(request, f'Renamed to “{name}”.')
    return redirect('it_role_detail', pk=pk)


@it_required
@require_POST
def it_role_add_member(request, pk):
    group = get_object_or_404(Group, pk=pk)
    ids = [v for v in request.POST.getlist('people') if str(v).isdigit()]
    people = list(User.objects.filter(pk__in=ids))
    if not people:
        _tell(request, 'Choose somebody to add.', 'error')
        return redirect('it_role_detail', pk=pk)
    added = [u for u in people if not u.groups.filter(pk=group.pk).exists()]
    group.user_set.add(*people)
    for u in added:
        ITAuditLog.write(request, ITAuditLog.Action.ROLE_GRANT,
                         f'{u.get_full_name() or u.username} was given {group.name}',
                         target=u.username)
    _tell(request, f'{len(added)} added to {group.name}.' if added else 'They already had it.')
    return redirect('it_role_detail', pk=pk)


@it_required
@require_POST
def it_role_remove_member(request, pk, user_pk):
    group = get_object_or_404(Group, pk=pk)
    person = get_object_or_404(User, pk=user_pk)
    # Emptying Intranet Admin or IT would leave the portal reachable only
    # through the Django back-office and its one superuser account.
    if group.name in PROTECTED_ROLES and group.user_set.count() <= 1:
        _tell(request, f'{group.name} needs at least one member. Add somebody first.', 'error')
        return redirect('it_role_detail', pk=pk)
    group.user_set.remove(person)
    ITAuditLog.write(request, ITAuditLog.Action.ROLE_REVOKE,
                     f'{person.get_full_name() or person.username} lost {group.name}',
                     target=person.username)
    _tell(request, f'{person.get_full_name() or person.username} no longer holds {group.name}.')
    return redirect('it_role_detail', pk=pk)


@it_required
@require_POST
def it_role_create(request):
    name = (request.POST.get('name') or '').strip()[:80]
    if not name:
        _tell(request, 'Give the role a name.', 'error')
    elif Group.objects.filter(name__iexact=name).exists():
        _tell(request, f'A role called “{name}” already exists.', 'error')
    else:
        Group.objects.create(name=name)
        ITAuditLog.write(request, ITAuditLog.Action.ROLE_CREATE, f'Role “{name}” created', target=name)
        _tell(request, f'Role “{name}” created. It unlocks nothing until the code checks for it.')
    return redirect('it_roles')


# ── accounts ────────────────────────────────────────────────────────────────

@it_required
def it_users(request):
    q = (request.GET.get('q') or '').strip()
    state = request.GET.get('state', '')
    role = request.GET.get('role', '')

    people = User.objects.prefetch_related('groups').select_related('employee')
    if q:
        people = people.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q)
                               | Q(username__icontains=q) | Q(email__icontains=q))
    if state == 'active':
        people = people.filter(is_active=True)
    elif state == 'disabled':
        people = people.filter(is_active=False)
    elif state == 'privileged':
        people = people.filter(Q(is_superuser=True) | Q(groups__isnull=False))
    elif state == 'never':
        people = people.filter(last_login__isnull=True)
    if role:
        people = people.filter(groups__name=role)

    people = people.distinct().order_by('first_name', 'last_name', 'username')
    total = people.count()

    return render(request, 'core/it/users.html', {
        'tab': 'users',
        'people': people[:300],
        'total': total,
        'shown': min(total, 300),
        'q': q, 'state': state, 'role': role,
        'all_roles': Group.objects.order_by('name'),
    })


@it_required
def it_user_detail(request, pk):
    person = get_object_or_404(
        User.objects.prefetch_related('groups').select_related('employee'), pk=pk)
    return render(request, 'core/it/user_detail.html', {
        'tab': 'users',
        'person': person,
        'all_roles': Group.objects.order_by('name'),
        'held': set(person.groups.values_list('pk', flat=True)),
        'is_self': person.pk == request.user.pk,
    })


@it_required
@require_POST
def it_user_save(request, pk):
    person = get_object_or_404(User, pk=pk)
    before = {'first': person.first_name, 'last': person.last_name, 'email': person.email}
    person.first_name = (request.POST.get('first_name') or '').strip()[:150]
    person.last_name = (request.POST.get('last_name') or '').strip()[:150]
    person.email = (request.POST.get('email') or '').strip()[:254]
    person.save(update_fields=['first_name', 'last_name', 'email'])

    # Roles, all at once, so one save is one audit entry per change.
    chosen = {int(v) for v in request.POST.getlist('roles') if str(v).isdigit()}
    held = set(person.groups.values_list('pk', flat=True))
    names = dict(Group.objects.values_list('pk', 'name'))
    for pk_add in chosen - held:
        person.groups.add(pk_add)
        ITAuditLog.write(request, ITAuditLog.Action.ROLE_GRANT,
                         f'{person.username} was given {names.get(pk_add, pk_add)}',
                         target=person.username)
    for pk_rm in held - chosen:
        name = names.get(pk_rm, str(pk_rm))
        if name in PROTECTED_ROLES and Group.objects.get(pk=pk_rm).user_set.count() <= 1:
            _tell(request, f'{name} needs at least one member; it was left alone.', 'warning')
            continue
        person.groups.remove(pk_rm)
        ITAuditLog.write(request, ITAuditLog.Action.ROLE_REVOKE,
                         f'{person.username} lost {name}', target=person.username)

    changed = [k for k, v in before.items() if v != getattr(person, {'first': 'first_name', 'last': 'last_name', 'email': 'email'}[k])]
    if changed:
        ITAuditLog.write(request, ITAuditLog.Action.USER_EDIT,
                         f'{person.username}: changed {", ".join(changed)}', target=person.username)
    _tell(request, 'Account saved.')
    return redirect('it_user_detail', pk=pk)


@it_required
@require_POST
def it_user_toggle(request, pk):
    person = get_object_or_404(User, pk=pk)
    if person.pk == request.user.pk:
        _tell(request, 'You cannot disable your own account.', 'error')
        return redirect('it_user_detail', pk=pk)
    if person.is_superuser and person.is_active:
        _tell(request, 'Superuser accounts are disabled from the back-office, deliberately.', 'error')
        return redirect('it_user_detail', pk=pk)
    person.is_active = not person.is_active
    person.save(update_fields=['is_active'])
    action = (ITAuditLog.Action.USER_ACTIVATE if person.is_active
              else ITAuditLog.Action.USER_DEACTIVATE)
    ITAuditLog.write(request, action,
                     f'{person.get_full_name() or person.username} was '
                     f'{"enabled" if person.is_active else "disabled"}',
                     target=person.username)
    _tell(request, f'Account {"enabled" if person.is_active else "disabled"}.')
    return redirect('it_user_detail', pk=pk)


# ── directory (LDAP) ────────────────────────────────────────────────────────

@it_required
def it_directory(request):
    from . import ldap_auth
    config = ldap_auth.get_config()
    q = (request.GET.get('q') or '').strip()
    results, message, ok = [], '', None
    if request.GET.get('search') is not None:
        ok, message, results = ldap_auth.search_directory(q)
        known = set(User.objects.filter(
            username__in=[p['username'] for p in results]).values_list('username', flat=True))
        for p in results:
            p['exists'] = p['username'] in known
    return render(request, 'core/it/directory.html', {
        'tab': 'directory',
        'config': config,
        'configured': ldap_auth.is_configured(config),
        'q': q, 'ok': ok, 'message': message, 'results': results,
        'searched': request.GET.get('search') is not None,
    })


@it_required
@require_POST
def it_directory_import(request):
    """Create or refresh Hub accounts from the directory.

    Refresh rather than overwrite: name and email come from the directory, and
    roles and everything else the Hub knows are left exactly as they are.
    """
    from . import ldap_auth
    usernames = [u.strip().lower() for u in request.POST.getlist('usernames') if u.strip()]
    if not usernames:
        _tell(request, 'Nobody was selected.', 'error')
        return redirect('it_directory')

    ok, message, people = ldap_auth.search_directory('', limit=1000)
    if not ok:
        _tell(request, message, 'error')
        return redirect('it_directory')

    by_name = {p['username']: p for p in people}
    created = updated = 0
    for username in usernames:
        p = by_name.get(username)
        if not p:
            continue
        user, was_new = User.objects.get_or_create(
            username=username,
            defaults={'first_name': p['first'][:150], 'last_name': p['last'][:150],
                      'email': p['email'][:254], 'is_active': True})
        if was_new:
            user.set_unusable_password()   # they sign in through the directory
            user.save(update_fields=['password'])
            created += 1
        else:
            user.first_name = p['first'][:150] or user.first_name
            user.last_name = p['last'][:150] or user.last_name
            user.email = p['email'][:254] or user.email
            user.save(update_fields=['first_name', 'last_name', 'email'])
            updated += 1
    ITAuditLog.write(request, ITAuditLog.Action.LDAP_SYNC,
                     f'Directory import: {created} created, {updated} refreshed')
    _tell(request, f'{created} account(s) created, {updated} refreshed from the directory.')
    return redirect('it_directory')


# ── backups ─────────────────────────────────────────────────────────────────

def _report_command():
    """The scheduler lines for the weekly log report, for both platforms.

    The backup's own lines are built by core.backups.schedule_command(), from
    what the operator chose; this one has no choices to honour, so it stays
    here. Both take care to emit a line that actually runs when pasted: the
    first version printed a cron line containing Windows paths, and a schtasks
    line broken over two lines with unescaped quotes inside /tr.
    """
    from django.conf import settings

    python = sys.executable
    project = str(settings.BASE_DIR)
    on_windows = os.name == 'nt'
    linux_path = project.replace('\\', '/') if not on_windows else '/srv/ajihub'
    inner = f'\\"{python}\\" \\"{os.path.join(project, "manage.py")}\\" log_report'
    return {
        'windows': f'schtasks /create /tn "AJi Hub log report" '
                   f'/sc WEEKLY /d MON /st 03:00 /tr "{inner}" /f',
        'linux': f'0 3 * * 1 cd {linux_path} && '
                 f'{linux_path}/.venv/bin/python manage.py log_report',
    }


def _reports():
    """The dated log reports on disk, newest first."""
    folder = os.path.join(backup_dir(), 'reports')
    if not os.path.isdir(folder):
        return []
    out = []
    for name in sorted(os.listdir(folder), reverse=True):
        if not name.endswith('.txt'):
            continue
        full = os.path.join(folder, name)
        try:
            stat = os.stat(full)
        except OSError:
            continue
        out.append({'name': name, 'size': _human(stat.st_size),
                    'when': datetime.fromtimestamp(stat.st_mtime)})
    return out


@it_required
def it_backups(request):
    from django.conf import settings

    from . import backups as bk

    records = BackupRecord.objects.select_related('taken_by')[:50]
    for r in records:
        r.on_disk = r.exists()
        r.can_restore = bk.restorable(r.filename)
    db_path = settings.DATABASES['default'].get('NAME', '')
    db_size = os.path.getsize(db_path) if db_path and os.path.exists(db_path) else 0
    media = _dir_size(getattr(settings, 'MEDIA_ROOT', ''), cap_seconds=1.5)
    folder = backup_dir()
    mirror = os.path.join(folder, 'files')
    chosen = bk.chosen_parts()

    return render(request, 'core/it/backups.html', {
        'tab': 'backups',
        'records': records,
        'folder': folder,
        'default_folder': os.path.join(str(settings.BASE_DIR), 'backups'),
        'db_size': db_size,
        # Formatted here rather than in the template: a negative means the walk
        # gave up early, and "at least 3.8 GB" is not something a filter can say.
        'media_size': _human(media),
        'mirror_size': _human(_dir_size(mirror, cap_seconds=1.5)) if os.path.isdir(mirror) else None,
        'mirror_count': sum(len(f) for _r, _d, f in os.walk(mirror)) if os.path.isdir(mirror) else 0,
        'last_run': GlobalSetting.get(bk.KEY_LAST_RUN, ''),
        # What the operator chose: what goes in, how often, how many to keep.
        'parts': [{'key': k, 'label': lab, 'blurb': blurb, 'on': k in chosen}
                  for k, lab, blurb in bk.PARTS],
        'schedules': [{'key': k, 'label': lab, 'blurb': blurb,
                       'on': k == bk.chosen_schedule()}
                      for k, lab, blurb in bk.SCHEDULES],
        'schedule': bk.chosen_schedule(),
        'hour': bk.chosen_hour(),
        'keep': bk.keep_count(),
        'reports': _reports(),
        # Generated from those choices, so the line and the setting cannot drift.
        'cmd': {'backup': bk.schedule_command(),
                'report': _report_command(),
                'on_windows': os.name == 'nt',
                'linux_path': bk.schedule_command()['linux_path'],
                'linux_is_placeholder': os.name == 'nt'},
    })


@it_required
@require_POST
def it_backup_settings(request):
    """What a backup contains, how often it should run, and how many to keep.

    One form rather than three: the three answers are read together every time
    a backup runs, and saving them separately meant three round trips to change
    one policy.
    """
    from . import backups as bk

    parts = [p for p in request.POST.getlist('parts') if p in bk.PART_KEYS]
    if not parts:
        _tell(request, 'A backup has to include something — pick at least one.',
              'error')
        return redirect(reverse('it_backups') + '#policy')

    schedule = request.POST.get('schedule', '')
    schedule = schedule if schedule in bk.SCHEDULE_KEYS else 'daily'

    raw_hour = (request.POST.get('hour') or '').strip()
    hour = int(raw_hour) if raw_hour.isdigit() and 0 <= int(raw_hour) <= 23 \
        else bk.DEFAULT_HOUR

    raw_keep = (request.POST.get('keep') or '').strip()
    if not raw_keep.isdigit() or int(raw_keep) < 1:
        _tell(request, 'How many copies to keep must be a number, 1 or more.',
              'error')
        return redirect(reverse('it_backups') + '#policy')
    keep = int(raw_keep)

    GlobalSetting.set(bk.KEY_PARTS, ','.join(parts), request.user)
    GlobalSetting.set(bk.KEY_SCHEDULE, schedule, request.user)
    GlobalSetting.set(bk.KEY_HOUR, str(hour), request.user)
    GlobalSetting.set(bk.KEY_KEEP, str(keep), request.user)

    labels = dict((k, lab) for k, lab, _b in bk.PARTS)
    every = dict((k, lab) for k, lab, _b in bk.SCHEDULES)[schedule]
    ITAuditLog.write(
        request, ITAuditLog.Action.SETTING,
        f'Backup policy: {", ".join(labels[p] for p in parts)} · {every.lower()}'
        + ('' if schedule == 'off' else f' at {hour:02d}:00')
        + f' · keep {keep}')
    _tell(request, 'Backup settings saved.')
    return redirect(reverse('it_backups') + '#policy')


@it_required
@require_POST
def it_report_now(request):
    """Write a log report on demand, the same one the weekly job writes."""
    from django.core.management import call_command
    days = request.POST.get('days', '7')
    try:
        call_command('log_report', days=int(days) if str(days).isdigit() else 7, quiet=True)
        _tell(request, 'Report written to the reports folder.')
    except Exception as exc:
        _tell(request, f'The report could not be written: {exc}', 'error')
    return redirect(reverse('it_backups') + '#reports')


@it_required
def it_report_download(request, name):
    """Hand back one report. The name is rebuilt from a basename, so a path
    cannot be walked out of the reports folder with it."""
    safe = os.path.basename(name)
    if not safe.endswith('.txt'):
        raise Http404
    path = os.path.join(backup_dir(), 'reports', safe)
    if not os.path.exists(path):
        raise Http404
    return FileResponse(open(path, 'rb'), as_attachment=True, filename=safe)


def _human(n):
    if n is None:
        return 'unknown'
    prefix = 'at least ' if n < 0 else ''
    n = abs(n)
    for unit, step in (('GB', 1073741824), ('MB', 1048576), ('KB', 1024)):
        if n >= step:
            return f'{prefix}{n / step:.1f} {unit}'
    return f'{prefix}{n} bytes'


def _dir_size(path, cap_seconds=2.0):
    """Rough size of a folder, giving up rather than walking forever.

    Media here is gigabytes across tens of thousands of files; a page that waits
    for an exact number is a page that times out.
    """
    import time
    if not path or not os.path.isdir(path):
        return None
    started, total = time.time(), 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
        if time.time() - started > cap_seconds:
            return -total      # negative marks it as "at least this much"
    return total


@it_required
@require_POST
def it_backup_now(request):
    """Take a backup now, of whatever the policy says a backup contains.

    Same engine as the scheduled job — core.backups.perform — so the button and
    the nightly run cannot produce different things. The folder is created by
    it if somebody has deleted it.

    Including the uploaded files makes the *first* run slow: media is gigabytes
    across tens of thousands of files. Every run after it copies only what
    changed, so it is quick. The page says so beside the tick.
    """
    from . import backups as bk

    out = bk.perform(note=(request.POST.get('note') or '').strip(),
                     user=request.user, request=request)
    if out['errors'] and not out['db_name'] and not out['config_name']:
        _tell(request, 'Backup failed: ' + '; '.join(out['errors']), 'error')
    elif out['errors']:
        _tell(request, 'Backup taken, with problems: ' + bk.summary_line(out),
              'error')
    else:
        _tell(request, f'Backup taken — {bk.summary_line(out)}')
    return redirect('it_backups')


@it_required
def it_backup_restore(request, pk):
    """Put one backup back, after saying plainly what that means.

    A page rather than a dialog. This is the most destructive thing the portal
    can do — every change made since that copy was taken is gone — and a
    confirm() box is one stray Enter away from happening by accident. The word
    has to be typed, so it cannot be pressed through by muscle memory.
    """
    from . import backups as bk

    record = get_object_or_404(BackupRecord, pk=pk)
    if not bk.restorable(record.filename):
        _tell(request, 'Only a dated database copy can be restored.', 'error')
        return redirect('it_backups')

    if request.method == 'POST':
        if (request.POST.get('confirm') or '').strip().upper() != 'RESTORE':
            _tell(request, 'Type RESTORE to confirm. Nothing was changed.',
                  'error')
            return redirect('it_backup_restore', pk=pk)
        try:
            out = bk.restore(record.filename, user=request.user, request=request)
        except bk.RestoreError as exc:
            _tell(request, str(exc), 'error')
            return redirect('it_backup_restore', pk=pk)
        _tell(request, f'Restored from {out["restored"]}. The database as it '
                       f'was a moment ago is saved as {out["safety"]}.')
        return redirect('it_backups')

    return render(request, 'core/it/backup_restore.html', {
        'tab': 'backups',
        'record': record,
        'on_disk': record.exists(),
        'newer': BackupRecord.objects.filter(
            created_at__gt=record.created_at).count(),
    })


@it_required
def it_backup_download(request, pk):
    record = get_object_or_404(BackupRecord, pk=pk)
    path = os.path.join(backup_dir(), record.filename)
    # The name comes from the database, but the file is served from disk — so
    # the path is rebuilt from the folder and a basename, never from input.
    path = os.path.join(backup_dir(), os.path.basename(record.filename))
    if not os.path.exists(path):
        raise Http404
    return FileResponse(open(path, 'rb'), as_attachment=True, filename=record.filename)


@it_required
@require_POST
def it_backup_delete(request, pk):
    record = get_object_or_404(BackupRecord, pk=pk)
    path = os.path.join(backup_dir(), os.path.basename(record.filename))
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as exc:
        _tell(request, f'Could not remove the file: {exc}', 'error')
        return redirect('it_backups')
    ITAuditLog.write(request, ITAuditLog.Action.BACKUP_DELETE,
                     f'Backup deleted: {record.filename}', target=record.filename)
    record.delete()
    _tell(request, 'Backup deleted.')
    return redirect('it_backups')


@it_required
@require_POST
def it_backup_folder(request):
    path = (request.POST.get('folder') or '').strip()
    if path:
        try:
            os.makedirs(path, exist_ok=True)
        except Exception as exc:
            _tell(request, f'That folder cannot be used: {exc}', 'error')
            return redirect('it_backups')
    GlobalSetting.set('backup_dir', path, request.user)
    ITAuditLog.write(request, ITAuditLog.Action.SETTING,
                     f'Backup folder set to {path or "the default"}')
    _tell(request, 'Backup folder saved.')
    return redirect('it_backups')


# ── logs ────────────────────────────────────────────────────────────────────

LOG_PERIODS = [('7', 'Last 7 days'), ('30', 'Last 30 days'),
               ('90', 'Last 90 days'), ('', 'All time')]


@it_required
def it_logs(request):
    """The audit trail, with filters that say what they did.

    The filters worked before; they just could not prove it. Narrowing to a
    kind of event that has never happened gave an empty table under the words
    "Nothing recorded yet" and a footer reading "0 entries" — which reads as a
    log that has been wiped, not a filter that matched nothing. So: every kind
    carries its own count in the dropdown, the footer always names the size of
    the whole log, and the empty state distinguishes the two cases.
    """
    action = request.GET.get('action', '')
    q = (request.GET.get('q') or '').strip()
    period = request.GET.get('period', '')

    everything = ITAuditLog.objects.all()
    grand_total = everything.count()

    rows = ITAuditLog.objects.select_related('actor')
    if action:
        rows = rows.filter(action=action)
    if q:
        rows = rows.filter(Q(summary__icontains=q) | Q(target__icontains=q)
                           | Q(actor__username__icontains=q))
    if period.isdigit():
        rows = rows.filter(created_at__gte=timezone.now() - timedelta(days=int(period)))

    total = rows.count()

    # How many of each kind exist, so the dropdown shows what is there to find
    # rather than the full list of things the log is capable of recording.
    tally = dict(everything.values_list('action').annotate(n=Count('id')))
    choices = [(value, label, tally.get(value, 0))
               for value, label in ITAuditLog.Action.choices]
    choices.sort(key=lambda c: (-c[2], c[1]))

    oldest = everything.order_by('created_at').values_list('created_at', flat=True).first()

    # Paged rather than truncated. It used to hand back the most recent 200 and
    # say so in the footer, which meant the 201st entry was unreachable through
    # the interface — the log kept it and the portal would not show it. A filter
    # narrow enough to get under 200 was the only way to see anything older.
    page = Paginator(rows, LOG_PAGE_SIZE).get_page(request.GET.get('page'))

    return render(request, 'core/it/logs.html', {
        'tab': 'logs',
        'page': page,
        'rows': page.object_list,
        'page_window': _page_window(page),
        # Every link back to this page has to carry the filters, or paging
        # quietly widens the search back to everything.
        'filter_qs': urlencode([(k, v) for k, v in
                                (('q', q), ('action', action), ('period', period))
                                if v]),
        'total': total,
        'grand_total': grand_total,
        'filtered': bool(action or q or period),
        'oldest': oldest,
        'action': action, 'q': q, 'period': period,
        'periods': LOG_PERIODS,
        'actions': choices,
    })


#: Fifty rows is about one screen of scrolling, and keeps the query cheap on a
#: log that only ever grows.
LOG_PAGE_SIZE = 50


def _page_window(page, span=2):
    """Page numbers worth drawing: the ends, and a few either side of here.

    A log with two hundred pages should not render two hundred links. None in
    the list means "a gap" and the template draws an ellipsis for it.
    """
    last = page.paginator.num_pages
    if last <= 1:
        return []
    wanted = {1, last}
    wanted |= {n for n in range(page.number - span, page.number + span + 1)
               if 1 <= n <= last}
    out, previous = [], 0
    for n in sorted(wanted):
        if previous and n - previous > 1:
            out.append(None)
        out.append(n)
        previous = n
    return out


# ── security ────────────────────────────────────────────────────────────────

def _security_findings():
    """Checks worth an administrator's attention, computed live.

    These are the same questions the written access review asked, answered from
    the database each time rather than from a document that goes stale.
    """
    out = []
    supers = User.objects.filter(is_superuser=True, is_active=True)
    if supers.count() == 0:
        out.append(('bad', 'No superuser account',
                    'Nobody can reach the Django back-office.'))
    elif supers.count() == 1:
        out.append(('warn', 'One superuser account',
                    f'Only “{supers[0].username}”. If it is lost, roles cannot be '
                    f'granted by anyone.'))

    for name in PROTECTED_ROLES:
        g = Group.objects.filter(name=name).first()
        if g and g.user_set.filter(is_active=True).count() == 0:
            out.append(('warn', f'{name} has nobody in it',
                        'The portal is reachable only by a superuser.'))

    stale = User.objects.filter(is_active=True, last_login__isnull=True).filter(
        Q(is_superuser=True) | Q(groups__isnull=False)).distinct()
    if stale.exists():
        out.append(('warn', f'{stale.count()} privileged account(s) never signed in',
                    'An account holding a role that has never been used is worth '
                    'checking: ' + ', '.join(u.username for u in stale[:5])
                    + ('…' if stale.count() > 5 else '')))

    disabled_with_roles = User.objects.filter(is_active=False, groups__isnull=False).distinct()
    if disabled_with_roles.exists():
        out.append(('warn', f'{disabled_with_roles.count()} disabled account(s) still hold roles',
                    'Disabling stops sign-in but leaves the roles attached. Worth '
                    'clearing so the role lists mean what they say.'))

    # Somebody holding almost everything is a single point of both failure and access.
    hoarders = (User.objects.annotate(n=Count('groups'))
                .filter(n__gte=max(3, Group.objects.count() - 3)))
    for u in hoarders:
        out.append(('info', f'{u.get_full_name() or u.username} holds {u.n} of '
                            f'{Group.objects.count()} roles',
                    'Concentration is convenient and is also a single point of access. '
                    'Worth naming a deputy for the critical ones.'))

    empty = Group.objects.annotate(n=Count('user')).filter(n=0)
    if empty.exists():
        out.append(('info', f'{empty.count()} role(s) with no members',
                    ', '.join(g.name for g in empty) + '. Harmless, but if somebody was '
                    'meant to have these they do not.'))
    return out


@it_required
def it_security(request):
    from . import ldap_auth
    return render(request, 'core/it/security.html', {
        'tab': 'security',
        'findings': _security_findings(),
        'privileged': User.objects.filter(
            Q(is_superuser=True) | Q(groups__name__in=PROTECTED_ROLES)
        ).distinct().prefetch_related('groups').order_by('username'),
        'recent_signins': User.objects.filter(last_login__isnull=False)
                              .order_by('-last_login')[:12],
        'ldap_on': ldap_auth.get_config()['enabled'],
    })


# ── shared people search for the pickers ────────────────────────────────────

@it_required
def it_people_search(request):
    from .people import search_people
    exclude = [v for v in request.GET.getlist('exclude') if str(v).isdigit()]
    return JsonResponse({'users': search_people(request.GET.get('q', ''), exclude=exclude)})
