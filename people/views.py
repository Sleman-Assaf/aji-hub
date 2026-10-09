# people/views.py
import json

from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Q, Prefetch

from .models import Employee, Skill, SkillCategory, EmployeeSkill

#: How many people arrive with the page, and how many each "Show more" adds.
#: The directory used to render all 1,256 active employees into the HTML — every
#: card, for everybody, before the page could paint. The first screenful is what
#: anybody actually reads; the rest is fetched only if they ask for it.
FIRST_PAGE = 10
MORE_PAGE = 50
#: A hand-edited offset cannot ask for the whole table in one request.
MAX_PAGE = 100


def _page_bounds(request):
    """The offset and limit for a "show more" request, clamped."""
    try:
        offset = max(0, int(request.GET.get('offset') or 0))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = int(request.GET.get('limit') or MORE_PAGE)
    except (TypeError, ValueError):
        limit = MORE_PAGE
    return offset, max(1, min(limit, MAX_PAGE))


def _photo_url(emp):
    """An employee's photo URL, or '' when the file or the field is missing."""
    if not emp.photo:
        return ''
    try:
        return emp.photo.url
    except (ValueError, OSError):
        return ''


def _card_json(emp):
    """One employee, shaped for the card the directory's JavaScript builds."""
    return {
        'pk':       emp.pk,
        'name':     emp.full_name,
        'role':     emp.role or '',
        'entity':   emp.entity.short_name if emp.entity else '',
        'dept':     emp.department.name if emp.department else '',
        'email':    emp.email or '',
        'ext':      emp.extension or '',
        'photo':    _photo_url(emp),
        'initials': emp.initials(),
        'user_pk':  emp.user_id or 0,
    }


def _directory_queryset(request):
    """The filtered, ordered employee list behind the directory.

    Ordered explicitly: paging by offset over an unordered queryset is how the
    same person appears on two pages and somebody else on neither.
    """
    q = request.GET.get('q', '').strip()
    entity = request.GET.get('entity', '')
    dept = request.GET.get('dept', '')

    employees = Employee.objects.filter(is_active=True).select_related('entity', 'department')
    if q:
        employees = employees.filter(Q(full_name__icontains=q) | Q(role__icontains=q))
    if entity:
        employees = employees.filter(entity__code=entity)
    if dept:
        employees = employees.filter(department__name__icontains=dept)
    return employees.order_by('full_name', 'pk')


@login_required
def directory(request):
    employees = _directory_queryset(request)

    # Live search and "show more" both come through here.
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        offset, limit = _page_bounds(request)
        total = employees.count()
        page = list(employees[offset:offset + limit])
        return JsonResponse({
            'employees': [_card_json(e) for e in page],
            'total': total,
            'next_offset': offset + len(page),
            'has_more': offset + len(page) < total,
        })

    from people.models import Entity
    first = list(employees[:FIRST_PAGE])
    total = employees.count()
    return render(request, 'people/directory.html', {
        'employees': first,
        'entities': Entity.objects.all(),
        'q': request.GET.get('q', '').strip(),
        'selected_entity': request.GET.get('entity', ''),
        'total': total,
        'next_offset': len(first),
        'has_more': total > len(first),
        'more_page': MORE_PAGE,
    })


@login_required
def profile_detail(request, pk):
    employee = get_object_or_404(Employee, pk=pk, is_active=True)
    skills_by_cat = {}
    for es in employee.skills.select_related('skill__category').all():
        cat = es.skill.category.name
        skills_by_cat.setdefault(cat, []).append(es)

    direct_reports = employee.direct_reports.filter(is_active=True)

    # Recognition, if there is any. The block is left out entirely when the
    # count is zero — a profile reading "0 Kudos" says something about the
    # person that the Hub has no business saying.
    praise, praise_count, athar, certs = [], 0, [], []
    if employee.user_id:
        from taqdeer.models import Certification, Nomination, Praise, TaqdeerSettings
        received = Praise.objects.filter(recipient_id=employee.user_id,
                                         hidden_at__isnull=True)
        praise_count = received.count()
        praise = list(received.select_related('giver', 'giver__employee')[:4])
        athar = list(Nomination.objects
                     .filter(recipient_id=employee.user_id,
                             status=Nomination.Status.APPROVED)
                     .select_related('cycle')
                     .order_by('-cycle__year', '-cycle__quarter'))
        if TaqdeerSettings.get().certification_on_profile:
            certs = list(Certification.objects.filter(
                employee_id=employee.user_id, published=True))

    return render(request, 'people/profile_detail.html', {
        'employee': employee,
        'skills_by_cat': skills_by_cat,
        'direct_reports': direct_reports,
        'praise': praise,
        'praise_count': praise_count,
        'athar': athar,
        'certs': certs,
    })


@login_required
def reporting_line_api(request, pk):
    """Manager's own manager + manager's team (with this employee pinned/visible),
    for the expandable 'Reporting Line' panel on the profile page."""
    employee = get_object_or_404(Employee, pk=pk, is_active=True)
    manager = employee.manager
    if not manager:
        return JsonResponse({'manager': None})

    manager_manager = manager.manager

    team_qs = Employee.objects.filter(manager=manager, is_active=True).order_by('full_name')
    total = team_qs.count()
    SHOWN_LIMIT = 10
    shown = list(team_qs[:SHOWN_LIMIT])
    if not any(e.pk == employee.pk for e in shown) and total > SHOWN_LIMIT:
        shown = shown[:-1] + [employee]

    def emp_json(e, is_self=False):
        return {
            'id': e.pk,
            'name': e.full_name,
            'initials': e.initials(),
            'has_photo': bool(e.photo),
            'photo_url': e.photo.url if e.photo else '',
            'is_self': is_self,
        }

    return JsonResponse({
        'manager': {
            'id': manager.pk, 'name': manager.full_name, 'role': manager.role or '',
            'initials': manager.initials(), 'has_photo': bool(manager.photo),
            'photo_url': manager.photo.url if manager.photo else '',
        },
        'manager_manager': {
            'id': manager_manager.pk, 'name': manager_manager.full_name, 'role': manager_manager.role or '',
            'initials': manager_manager.initials(), 'has_photo': bool(manager_manager.photo),
            'photo_url': manager_manager.photo.url if manager_manager.photo else '',
        } if manager_manager else None,
        'team': [emp_json(e, is_self=(e.pk == employee.pk)) for e in shown],
        'team_count': total,
        'more_count': max(0, total - len(shown)),
    })


@login_required
def profile_modal(request, pk):
    """Partial for AJAX profile modal."""
    employee = get_object_or_404(Employee, pk=pk, is_active=True)
    skills_by_cat = {}
    for es in employee.skills.select_related('skill__category').all():
        cat = es.skill.category.name
        skills_by_cat.setdefault(cat, []).append(es)
    return render(request, 'people/profile_modal_partial.html', {
        'employee': employee,
        'skills_by_cat': skills_by_cat,
    })


@login_required
def _skills_queryset(request):
    """The filtered, ordered list behind the skills directory."""
    q = request.GET.get('q', '').strip()
    skill_ids = [x for x in request.GET.getlist('skill') if x.isdigit()]
    country = request.GET.get('country', '')
    entity = request.GET.get('entity', '')

    employees = Employee.objects.filter(is_active=True).prefetch_related(
        Prefetch('skills', queryset=EmployeeSkill.objects.select_related('skill__category'))
    ).select_related('entity', 'department')

    if q:
        employees = employees.filter(
            Q(full_name__icontains=q) | Q(role__icontains=q) |
            Q(skills__skill__name__icontains=q)
        ).distinct()
    # Filtered one at a time on purpose: chaining the conditions means "has all
    # of these skills", where a single __in would mean "has any of them", and
    # the point of picking three skills is to narrow the list rather than widen it.
    for sid in skill_ids:
        employees = employees.filter(skills__skill__pk=sid)
    if skill_ids:
        employees = employees.distinct()
    if country:
        employees = employees.filter(country__icontains=country)
    if entity:
        employees = employees.filter(entity__code=entity)
    return employees.order_by('full_name', 'pk')


def _skill_card_json(emp, selected):
    """One person and their first few skills, for the JavaScript card."""
    rows = list(emp.skills.all())
    return {
        'pk':       emp.pk,
        'name':     emp.full_name,
        'role':     emp.role or '',
        'entity':   emp.entity.name if emp.entity else '',
        'photo':    _photo_url(emp),
        'initials': emp.initials(),
        'skills':   [{'name': es.skill.name, 'level': es.level or 0,
                      'match': es.skill_id in selected} for es in rows[:6]],
        'more':     max(0, len(rows) - 6),
    }


@login_required
def skills_directory(request):
    """AJi Skills.

    The filtering used to happen in the browser: every active employee and every
    one of their skills was rendered into the page, and typing hid the cards that
    did not match. That is instant on a hundred people and a 1,256-card document
    on this directory — and it cannot page, because a search that only looks at
    what is already loaded is not a search.
    """
    employees = _skills_queryset(request)
    selected = {int(x) for x in request.GET.getlist('skill') if x.isdigit()}

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        offset, limit = _page_bounds(request)
        total = employees.count()
        page = list(employees[offset:offset + limit])
        return JsonResponse({
            'employees': [_skill_card_json(e, selected) for e in page],
            'total': total,
            'next_offset': offset + len(page),
            'has_more': offset + len(page) < total,
        })

    from people.models import Entity
    first = list(employees[:FIRST_PAGE])
    total = employees.count()
    return render(request, 'people/skills.html', {
        'employees': first,
        'categories': SkillCategory.objects.prefetch_related('skills').all(),
        'skills_flat': Skill.objects.select_related('category').all(),
        'entities': Entity.objects.all(),
        'q': request.GET.get('q', '').strip(),
        'selected_entity': request.GET.get('entity', ''),
        'selected_skills': sorted(selected),
        'total': total,
        'next_offset': len(first),
        'has_more': total > len(first),
        'more_page': MORE_PAGE,
    })


@login_required
def phone_directory(request):
    q      = request.GET.get('q', '').strip()
    office = request.GET.get('office', '').strip()
    dept   = request.GET.get('dept', '').strip()

    employees = Employee.objects.filter(
        is_active=True
    ).exclude(extension='').select_related('department', 'entity').order_by('full_name')

    if q:
        employees = employees.filter(
            Q(full_name__icontains=q) | Q(extension__icontains=q) | Q(role__icontains=q)
        )
    if office:
        employees = employees.filter(office__icontains=office)
    if dept:
        employees = employees.filter(department__name__icontains=dept)

    all_offices = Employee.objects.filter(is_active=True).exclude(office='') \
        .values_list('office', flat=True).distinct().order_by('office')

    from people.models import Department
    all_depts = Department.objects.filter(
        employee__is_active=True
    ).exclude(employee__extension='').values_list('name', flat=True).distinct().order_by('name')

    from collections import defaultdict
    grouped = defaultdict(list)
    for emp in employees:
        dept_name = emp.department.name if emp.department else (emp.office or 'Other')
        grouped[dept_name].append(emp)

    grouped = dict(sorted(grouped.items()))

    return render(request, 'people/phone_directory.html', {
        'grouped':     grouped,
        'employees':   employees,
        'all_offices': list(all_offices),
        'all_depts':   list(all_depts),
        'q':           q,
        'sel_office':  office,
        'sel_dept':    dept,
        'total':       employees.count(),
    })


# ── Organisational hierarchy ───────────────────────────────────────────────
#
# The directory's own data does not describe one company. It is seven HR
# exports, one per entity, and the same person — the CEO — appears once in each
# of them as a separate employee record with no manager. Read literally that is
# fourteen unconnected trees, which is what the old chart drew and why it was
# unreadable: there was no top.
#
# So the shape is composed here rather than stored. Nothing below writes to the
# database, deliberately: the duplicates are a property of how HR exports, and
# the next import would recreate any merge done to the records themselves.

#: The person every one of those duplicate records refers to.
CEO_NAME_KEY = 'hamzeh awwad'
#: Seeded accounts for the job-requisition demo. They have no place on a chart
#: of the company and are identified by what they say they are.
DEMO_ROLE_MARK = 'demo account'

#: A spreadsheet header row that was imported as an employee. It calls itself
#: "Employee Name", works as a "Position", and its employee code is the string
#: "Emp. Code" — matching on the code is what makes this safe, because a real
#: colleague's code is a number. It still appears in the directory; this only
#: keeps it off the hierarchy, where it was drawn as its own company.
PLACEHOLDER_CODES = {'emp. code', 'emp code', 'employee code'}


def _is_placeholder(emp):
    return (DEMO_ROLE_MARK in (emp.role or '').lower()
            or (emp.emp_code or '').strip().lower() in PLACEHOLDER_CODES)

BOARD_ID = 'board'
CEO_ID = 'ceo'


def _norm_name(name):
    return ' '.join((name or '').split()).lower()


def _org_index():
    """Everyone active, indexed for walking the hierarchy.

    Returns (by_id, ceo_ids, children, ceo). `children[None]` is everybody who
    belongs directly under the CEO — whether they report to one of the duplicate
    CEO records or to nobody at all, which on this data is the difference
    between a country manager with 104 people under him and a floating root.
    """
    people = (Employee.objects.filter(is_active=True)
              .select_related('entity', 'department', 'manager')
              .order_by('full_name', 'pk'))

    by_id, ceo_ids = {}, set()
    for e in people:
        if _is_placeholder(e):
            continue
        by_id[e.pk] = e
        if _norm_name(e.full_name) == CEO_NAME_KEY:
            ceo_ids.add(e.pk)

    children = {}
    for e in by_id.values():
        if e.pk in ceo_ids:
            continue
        mid = e.manager_id
        # A manager who is a CEO record, is inactive, or is a hidden demo
        # account all mean the same thing here: this person sits at the top of
        # their entity, under the CEO.
        if mid in ceo_ids or mid not in by_id:
            mid = None
        children.setdefault(mid, []).append(e.pk)

    # The record that gets shown. Preference to the one that actually says CEO;
    # the rest say Managing Director because that is their title in that entity.
    ceo = None
    for pk in ceo_ids:
        emp = by_id[pk]
        if 'chief executive' in (emp.role or '').lower():
            ceo = emp
            break
    if ceo is None and ceo_ids:
        ceo = by_id[min(ceo_ids)]
    return by_id, ceo_ids, children, ceo


def _entity_key(emp):
    ent = emp.entity
    return (ent.code if ent and ent.code else 'other',
            ent.name if ent and ent.name else 'Other')


def _org_person(emp, children):
    """One person as a node for the explorer."""
    return {
        'id': f'emp:{emp.pk}',
        'pk': emp.pk,
        'kind': 'person',
        'name': emp.full_name,
        'role': emp.role or '',
        'dept': emp.department.name if emp.department else '',
        'entity': emp.entity.name if emp.entity else '',
        'initials': emp.initials(),
        'photo': _photo_url(emp),
        'email': emp.email or '',
        'count': len(children.get(emp.pk, [])),
    }


def _org_tree():
    """The whole hierarchy as one nested structure, for drawing as a diagram.

    Sent in full rather than a level at a time. 1,251 people come to roughly
    150 KB of this, which is less than one of the photographs on the page, and
    it buys a diagram that expands, collapses and jumps to a searched name
    without going back to the server for any of it — the drill-down it replaces
    cost a request per click.

    Keys are single letters because they repeat 1,251 times: n name, r role,
    i initials, p photo, t how many people sit underneath, c children.

    `seen` guards against a manager cycle. The data is not proof against one —
    two people can each be recorded as the other's manager — and without the
    guard that is not a wrong diagram but an infinite one.
    """
    by_id, ceo_ids, children, ceo = _org_index()

    def person(pk, seen):
        if pk in seen:
            return None
        seen = seen | {pk}
        emp = by_id[pk]
        kids = [person(c, seen) for c in children.get(pk, ())]
        kids = [k for k in kids if k]
        return {
            'id': f'e{pk}', 'k': 'person', 'n': emp.full_name,
            'r': emp.role or '', 'i': emp.initials(), 'p': _photo_url(emp) or '',
            'd': emp.department.name if emp.department else '',
            't': _subtree_size(pk, children),
            'c': kids,
        }

    # Under the CEO, by company. Seven or eight entities is a diagram; 160
    # names hanging off one box is not.
    groups = {}
    for pk in children.get(None, []):
        code, label = _entity_key(by_id[pk])
        groups.setdefault(code, {'label': label, 'members': []})['members'].append(pk)

    entity_nodes = []
    for code, g in groups.items():
        kids = [person(pk, set()) for pk in g['members']]
        kids = [k for k in kids if k]
        entity_nodes.append({
            'id': f'n{code}', 'k': 'entity', 'n': g['label'],
            'r': f'{len(kids)} reporting directly', 'i': (g['label'][:2] or '??').upper(),
            'p': '', 'd': '',
            't': sum(1 + k['t'] for k in kids),
            'c': sorted(kids, key=lambda k: (-k['t'], k['n'])),
        })
    entity_nodes.sort(key=lambda n: (-n['t'], n['n']))

    ceo_node = None
    if ceo is not None:
        ceo_node = {
            'id': 'ceo', 'k': 'ceo', 'n': ceo.full_name,
            'r': ceo.role or 'Chief Executive Officer',
            'i': ceo.initials(), 'p': _photo_url(ceo) or '', 'd': 'AJi Group',
            't': sum(1 + n['t'] for n in entity_nodes), 'c': entity_nodes,
        }

    return {
        'id': 'board', 'k': 'board', 'n': 'Board of Directors',
        'r': 'Governance', 'i': 'BD', 'p': '', 'd': '',
        't': (ceo_node['t'] + 1) if ceo_node else 0,
        'c': [ceo_node] if ceo_node else [],
    }


def _subtree_size(pk, children, seen=None):
    """Everyone underneath somebody, counted once even if the data loops."""
    if seen is None:
        seen = set()
    total = 0
    for child in children.get(pk, ()):
        if child in seen:
            continue
        seen.add(child)
        total += 1 + _subtree_size(child, children, seen)
    return total


@login_required
def org_chart(request):
    """The hierarchy as a diagram: Board, the CEO, the companies, the people.

    It used to be a drill-down — one box and its children, a request per click.
    That answered "who reports to whom" but never showed the shape, which is the
    only reason to draw a hierarchy at all.

    The whole tree goes to the page at once and is drawn there, so expanding,
    collapsing and jumping to a searched name all happen without a round trip.
    """
    tree = _org_tree()
    by_id, ceo_ids, children, ceo = _org_index()
    return render(request, 'people/org_chart.html', {
        'tree_json': json.dumps(tree, separators=(',', ':')),
        'total_count': len(by_id),
        'entity_count': len(tree['c'][0]['c']) if tree['c'] else 0,
        'ceo_records': len(ceo_ids),
        # How many are drawn under the CEO only because no manager is recorded
        # for them. Worth saying out loud: the diagram makes the gap visible
        # where the drill-down hid it one level down.
        'unplaced': sum(1 for pk in children.get(None, [])
                        if not by_id[pk].manager_id),
    })


@login_required
def org_search(request):
    """Anyone by name, for jumping straight to them in the hierarchy."""
    q = (request.GET.get('q') or '').strip()
    if len(q) < 2:
        return JsonResponse({'results': []})
    people = [e for e in Employee.objects.filter(is_active=True, full_name__icontains=q)
              .select_related('entity').order_by('full_name')[:20]
              if not _is_placeholder(e)][:12]
    return JsonResponse({'results': [{
        'pk': e.pk,
        'name': e.full_name,
        'role': e.role or '',
        'entity': e.entity.name if e.entity else '',
        'initials': e.initials(),
        'photo': _photo_url(e),
    } for e in people]})


@login_required
def org_node_partial(request, pk):
    """Returns HTML for one expanded org node."""
    employee = get_object_or_404(Employee, pk=pk)
    reports = employee.direct_reports.filter(is_active=True).select_related('entity')
    return render(request, 'people/org_node_partial.html', {
        'employee': employee,
        'reports': reports,
    })


@login_required
def qr_codes(request):
    """The WhatsApp channel QR codes.

    The codes were two emoji standing in for pictures that were already in the
    database: WhatsAppChannel has carried an uploaded qr_image since the People &
    Culture channels were built, and this page ignored it.
    """
    from content.models import WhatsAppChannel
    return render(request, 'people/qr_codes.html', {
        'qr_channels': WhatsAppChannel.objects.filter(is_active=True),
        # Same test the management screen uses, so the manage link appears for
        # exactly the people who can follow it.
        'is_hr': request.user.is_superuser
                 or request.user.groups.filter(name__in=['HR', 'HR Managers']).exists(),
    })


# ── MY PROFILE ───────────────────────────────────────────────────────────────

@login_required
def my_profile(request):
    """Redirect to the logged-in user's employee profile edit page."""
    try:
        emp = request.user.employee
        return redirect('profile_edit')
    except Exception:
        return render(request, 'people/my_profile_no_emp.html', {})


@login_required
def profile_edit(request):
    """
    View / edit the current user's own profile:
    - Photo upload
    - Bio
    - CV upload
    - Skills management
    """
    try:
        emp = request.user.employee
    except Exception:
        return render(request, 'people/my_profile_no_emp.html', {})

    all_skills    = Skill.objects.select_related('category').order_by('category__name', 'name')
    my_skill_ids  = set(emp.skills.values_list('skill_id', flat=True))
    my_skills     = emp.skills.select_related('skill', 'skill__category').all()

    if request.method == 'POST':
        action = request.POST.get('action', 'profile')

        if action == 'profile':
            import os
            update_fields = []

            # Update photo — from cropped base64 or direct file
            import base64
            from django.core.files.base import ContentFile

            cropped_b64 = request.POST.get('photo_cropped', '').strip()
            photo_fname = request.POST.get('photo_filename', 'photo.jpg').strip() or 'photo.jpg'

            if cropped_b64 and cropped_b64.startswith('data:'):
                # Decode base64 cropped image
                try:
                    header, data = cropped_b64.split(',', 1)
                    img_bytes = base64.b64decode(data)
                    if emp.photo:
                        try:
                            if os.path.isfile(emp.photo.path):
                                os.remove(emp.photo.path)
                        except Exception:
                            pass
                    # Save as JPEG
                    fname = os.path.splitext(photo_fname)[0] + '.jpg'
                    emp.photo.save(fname, ContentFile(img_bytes), save=False)
                    update_fields.append('photo')
                except Exception as ex:
                    pass
            elif 'photo' in request.FILES:
                if emp.photo:
                    try:
                        if os.path.isfile(emp.photo.path):
                            os.remove(emp.photo.path)
                    except Exception:
                        pass
                emp.photo = request.FILES['photo']
                update_fields.append('photo')

            # Update CV
            if 'cv' in request.FILES:
                if emp.cv:
                    try:
                        if os.path.isfile(emp.cv.path):
                            os.remove(emp.cv.path)
                    except Exception:
                        pass
                emp.cv = request.FILES['cv']
                update_fields.append('cv')

            # Update bio and linkedin only if submitted from infoForm
            if 'bio' in request.POST:
                emp.bio = request.POST.get('bio', '').strip()
                update_fields.append('bio')
            if 'linkedin' in request.POST:
                emp.linkedin = request.POST.get('linkedin', '').strip()
                update_fields.append('linkedin')

            if update_fields:
                emp.save(update_fields=update_fields)

            from django.contrib import messages as dj_messages
            dj_messages.success(request, 'Profile updated successfully.')
            return redirect('profile_edit')

        elif action == 'skills':
            # Replace skill set
            selected_ids = set(int(x) for x in request.POST.getlist('skills') if x.isdigit())
            levels       = request.POST.dict()   # skill_level_<id> -> level

            # Remove skills not selected
            emp.skills.exclude(skill_id__in=selected_ids).delete()

            # Add / update selected skills
            for skill_id in selected_ids:
                level_key = f'skill_level_{skill_id}'
                level     = int(levels.get(level_key, 2))
                EmployeeSkill.objects.update_or_create(
                    employee=emp,
                    skill_id=skill_id,
                    defaults={'level': level},
                )
            from django.contrib import messages as dj_messages
            dj_messages.success(request, 'Skills updated.')
            return redirect('profile_edit')

        elif action == 'remove_cv':
            if emp.cv:
                import os
                if os.path.isfile(emp.cv.path):
                    os.remove(emp.cv.path)
                emp.cv = None
                emp.save(update_fields=['cv'])
            return redirect('profile_edit')

        elif action == 'remove_photo':
            if emp.photo:
                import os
                if os.path.isfile(emp.photo.path):
                    os.remove(emp.photo.path)
                emp.photo = None
                emp.save(update_fields=['photo'])
            return redirect('profile_edit')

    # Group skills by category
    from itertools import groupby
    skills_by_cat = {}
    for skill in all_skills:
        cat = skill.category.name
        skills_by_cat.setdefault(cat, [])
        skills_by_cat[cat].append({
            'id':       skill.pk,
            'name':     skill.name,
            'selected': skill.pk in my_skill_ids,
            'level':    next((s.level for s in my_skills if s.skill_id == skill.pk), 2),
        })

    return render(request, 'people/profile_edit.html', {
        'emp':           emp,
        'my_skills':     my_skills,
        'skills_by_cat': skills_by_cat,
        'level_labels':  dict(EmployeeSkill.Level.choices),
    })


@login_required
def api_people_search(request):
    """GET /people/api/search/?q=name — for compact chat new-conversation picker."""
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'users': []})
    from django.contrib.auth.models import User
    users = User.objects.filter(is_active=True).filter(
        Q(first_name__icontains=q) | Q(last_name__icontains=q) |
        Q(username__icontains=q) |
        Q(employee__full_name__icontains=q)
    ).exclude(pk=request.user.pk).select_related('employee')[:10]
    data = []
    for u in users:
        name = getattr(u, 'employee', None)
        data.append({
            'id':   u.pk,
            'name': name.full_name if name else u.get_full_name() or u.username,
            'role': name.role if name else '',
            # The @mention autocomplete inserts this verbatim into the comment.
            'username': u.username,
        })
    return JsonResponse({'users': data})
