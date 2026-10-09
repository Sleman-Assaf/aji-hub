# core/search.py
"""One search box, across the whole Hub.

It used to look in six places — people, news, projects, policies, skills and
thread titles — which meant a search for a page, a service, a community, a
brand template or something on the cafeteria menu came back empty and looked
like a broken search box.

Every source is one entry in PROVIDERS below: a label, an icon, and a function
that takes the query and the reader and returns rows. Adding a source is adding
one function; nothing else in the file has to know about it.

Two rules every provider follows.

**It answers for the reader, not for the database.** A private community's
threads, a page limited to a group, a policy library with its own list of who
may open it — none of them may appear for somebody who cannot open them.
Listing a title is disclosure even when the link would refuse.

**It never searches anything personal.** Chat messages, feedback, anonymous
suggestions, training requests and notifications are deliberately absent, and
they are absent by omission rather than by a filter that could be loosened by
accident. Calendar entries are searched only when marked visible to everyone.
"""
from django.db.models import Q

from .models import is_intranet_admin

#: A per-source cap, so one popular word cannot fill the panel with 377
#: projects and push everything else out.
PER_SOURCE = 4
TOTAL = 20
MIN_QUERY = 2


# ─────────────────────────────────────────────────────────────────────────────
#  ranking
# ─────────────────────────────────────────────────────────────────────────────

def _score(label, q):
    """How well `label` answers `q`. Higher is better.

    Results used to come back grouped by source in a fixed order, so a person
    whose job title happened to contain the word outranked a page named exactly
    it. Rank on the match itself and the grouping stops mattering.
    """
    label, q = (label or '').lower(), q.lower()
    if label == q:
        return 100
    words = label.replace('-', ' ').replace('&', ' ').split()
    # A whole word of the label *is* the query. This outranks a prefix so that
    # "taqdeer" finds "AJi Taqdeer" before "Taqdeer settings" — the page some-
    # body means is the one the word names, not the first one alphabetically
    # that happens to start with it.
    if q in words:
        return 90
    if label.startswith(q):
        return 80
    # On a word boundary — "annual leave" for "leave" beats "believe".
    if any(word.startswith(q) for word in words):
        return 60
    if q in label:
        return 40
    return 20


# ─────────────────────────────────────────────────────────────────────────────
#  the sources
# ─────────────────────────────────────────────────────────────────────────────

#: The Hub's own destinations, and who may reach each. Searching for a *place*
#: is the commonest thing anybody types into a search box, and most of these
#: are invisible to every other provider: /taqdeer/ has no CMS page behind it,
#: so before this existed, typing "Taqdeer" returned nothing at all.
#:
#: (label, what to call it, url, who may see it). The gate is a function of the
#: user, or None for anybody signed in.
def _hr(user):
    from .models import user_is_hr
    return user_is_hr(user)


def _editor(user):
    from cms.models import user_can_edit_cms
    return user_can_edit_cms(user)


def _it(user):
    from .models import user_can_manage_settings
    return user_can_manage_settings(user)


DESTINATIONS = [
    ('Home', 'Page', '/', None),
    ('Employee Directory', 'People', '/people/', None),
    ('AJi Skills', 'People', '/people/skills/', None),
    ('Phone Extensions', 'People', '/people/extensions/', None),
    ('Org Hierarchy', 'People', '/people/org/', None),
    ('QR Codes', 'People', '/people/qr/', None),
    ('My profile', 'Your account', '/people/me/edit/', None),
    ('AJi News', 'Page', '/news/', None),
    ('My Calendar', 'Page', '/calendar/', None),
    ('AJi Exchange', 'Communities', '/exchange/', None),
    ('Chat', 'Messages, calls and screen sharing', '/messaging/', None),
    ('AJiAssist', 'Ask the Hub', '/assist/', None),
    ('My AJi', 'Services and requests', '/my-aji/', None),
    ('Approvals', 'Waiting on you', '/services/approvals/', None),
    ('Job Requisition', 'Request a new position',
     '/services/job-requisition/new/', None),
    ('Meeting Room Booking', 'Services', '/services/room-booking/', None),
    ('Cafeteria', 'Order to your desk', '/cafeteria/', None),
    ('Meeting Packages', 'Cafeteria', '/cafeteria/packages/', None),
    ('Training requests', 'Training and development', '/training/', None),
    ('SOPs & Manuals', 'Governance', '/policies/', None),
    ('Brand Files', 'Corporate identity', '/brand/', None),
    ('AJi Booth', 'Photographs', '/booth/', None),
    ('Onboarding Module', 'Handbook and welcome', '/onboarding/', None),
    ('People & Culture', 'HR', '/people-culture/', None),
    ('Project portfolio', 'Projects', '/portfolio/', None),
    ('AJi Taqdeer', 'Recognition', '/taqdeer/', None),
    ('My recognition', 'AJi Taqdeer', '/taqdeer/mine/', None),
    ('Recognition archive', 'AJi Taqdeer', '/taqdeer/archive/', None),
    ('Suggestion Box', 'Voice of the employee', '/voice/suggest/', None),
    ('Notification settings', 'Your account', '/notifications/settings/', None),
    ('All alerts', 'Your account', '/notifications/history/', None),
    # HR
    ('Athar Management', 'AJi Taqdeer · HR', '/taqdeer/athar/', _hr),
    ('Certifications', 'AJi Taqdeer · HR', '/taqdeer/certifications/', _hr),
    ('Taqdeer settings', 'AJi Taqdeer · HR', '/taqdeer/settings/', _hr),
    ('My submissions', 'AJi Taqdeer', '/taqdeer/submissions/', None),
    ('HR updates', 'People & Culture · HR', '/people-culture/hr/', _hr),
    # Editors
    ('Pages', 'Page builder', '/p/', _editor),
    ('Navigation and logo', 'Page builder', '/p/navigation/', _editor),
    ('Icons', 'Page builder', '/p/icons/', _editor),
    # IT
    ('IT portal', 'Administration', '/it/', _it),
    ('Roles & access', 'IT portal', '/it/roles/', _it),
    ('Accounts', 'IT portal', '/it/users/', _it),
    ('Backups', 'IT portal', '/it/backups/', _it),
    ('Audit log', 'IT portal', '/it/logs/', _it),
    ('Global settings', 'IT portal', '/it/integrations/', _it),
]


def _visible_destinations(user):
    out = []
    for label, sub, url, gate in DESTINATIONS:
        if gate is not None:
            try:
                if not gate(user):
                    continue
            except Exception:                                    # noqa: BLE001
                continue
        out.append({'label': label, 'sub': sub, 'url': url})
    return out


def destinations(q, user):
    """Places in the Hub, plus whatever the navigation currently points at.

    The navigation is editable, so a menu entry somebody adds tomorrow becomes
    searchable without this list being touched.
    """
    out, seen = [], set()
    needle = q.lower()
    for row in _visible_destinations(user):
        # The description counts as well as the name, so the words people
        # actually use find the page: "handbook" reaches Onboarding Module,
        # "recognition" reaches Taqdeer, "calls" reaches Chat.
        if needle in row['label'].lower() or needle in row['sub'].lower():
            out.append(row)
            seen.add(row['url'])

    from cms.models import NavItem
    for item in NavItem.objects.filter(label__icontains=q, is_visible=True):
        if not item.url or item.url in seen:
            continue
        try:
            if not item.is_visible_to(user):
                continue
        except Exception:                                        # noqa: BLE001
            continue
        out.append({'label': item.label, 'sub': 'In the menu', 'url': item.url})
        seen.add(item.url)
    return out[:PER_SOURCE]


def people(q, user):
    """Colleagues — by name, by job title, and by every identifier they have.

    Name and role only, before this: typing an extension, an employee number
    or an email address found nobody. A four-digit extension is exactly what
    somebody types when a call comes in from one, and the Phone Extensions page
    exists because that lookup is common.
    """
    from people.models import Employee
    # The same filter the directory uses. A spreadsheet header row was imported
    # as an employee — it calls itself "Emp. Code", holds the job title
    # "Position", and searching any employee number surfaced it.
    from people.views import _is_placeholder
    rows = Employee.objects.filter(
        Q(full_name__icontains=q) | Q(role__icontains=q)
        | Q(emp_code__icontains=q) | Q(extension__icontains=q)
        | Q(email__icontains=q) | Q(mobile_number__icontains=q)
        | Q(office__icontains=q),
        is_active=True).select_related('department')[:PER_SOURCE + 4]

    needle = q.lower().strip()
    out = []
    for e in rows:
        if _is_placeholder(e):
            continue
        if len(out) >= PER_SOURCE:
            break
        # Say *why* this person came back. A row reading only "Nael Shunnar"
        # for the query "1611" looks like a coincidence rather than an answer.
        why, score = [], None
        if needle and needle in (e.extension or '').lower():
            why.append(f'ext {e.extension}')
            score = 95 if needle == (e.extension or '').lower() else 70
        if needle and needle in (e.emp_code or '').lower():
            why.append(f'#{e.emp_code}')
            score = 95 if needle == (e.emp_code or '').lower() else 70
        if needle and '@' in needle and needle in (e.email or '').lower():
            why.append(e.email)
            score = 95
        if needle and needle in (e.office or '').lower() and not why:
            why.append(e.office)
        tail = e.role or (e.department.name if e.department_id else '')
        row = {'label': e.full_name,
               'sub': ' · '.join(why + ([tail] if tail else [])) or 'Colleague',
               'url': f'/people/{e.pk}/'}
        # The label carries none of the digits, so the usual scoring would put
        # an exact extension match at the very bottom of the panel.
        if score is not None:
            row['score'] = score
        out.append(row)
    return out


def pages(q, user):
    """CMS pages, honouring publication, the scheduling window and audience."""
    from cms.models import Page
    rows = (Page.objects.filter(Q(title__icontains=q) | Q(eyebrow__icontains=q)
                                | Q(subtitle__icontains=q),
                                is_published=True, draft_of__isnull=True)
            .prefetch_related('audience_groups'))
    out = []
    for page in rows:
        if page.is_archived or page.is_scheduled or not page.is_visible_to(user):
            continue
        out.append({'label': page.title,
                    'sub': page.eyebrow or 'Page',
                    'url': page.bound_path or f'/p/{page.slug}/'})
        if len(out) >= PER_SOURCE:
            break
    return out


def news(q, user):
    from content.models import NewsArticle
    rows = NewsArticle.objects.filter(
        Q(title__icontains=q) | Q(excerpt__icontains=q) | Q(body__icontains=q),
        published=True).select_related('category')[:PER_SOURCE]
    return [{'label': n.title,
             'sub': n.category.name if n.category_id else 'News',
             'url': f'/news/{n.slug}/'} for n in rows]


def hr_updates(q, user):
    """HR announcements, honouring their publish and archive dates."""
    from django.utils import timezone

    from content.models import HRAnnouncement
    now = timezone.now()
    rows = HRAnnouncement.objects.filter(
        Q(title__icontains=q) | Q(excerpt__icontains=q) | Q(body__icontains=q),
        published=True)
    out = []
    for a in rows:
        if a.publish_at and a.publish_at > now:
            continue
        if a.archive_at and a.archive_at <= now:
            continue
        out.append({'label': a.title, 'sub': a.tag or 'HR update',
                    'url': f'/people-culture/update/{a.slug}/'})
        if len(out) >= PER_SOURCE:
            break
    return out


def events(q, user):
    """Only entries marked visible to everyone — a diary is not searchable."""
    from content.models import CalendarEvent
    rows = CalendarEvent.objects.filter(
        Q(title__icontains=q) | Q(location__icontains=q),
        is_public=True).order_by('-date')[:PER_SOURCE]
    out = []
    for e in rows:
        # `%-d` for an unpadded day is a glibc extension and raises on Windows,
        # which is what this runs on. Pad and strip instead.
        when = f'{e.date:%d %b %Y}'.lstrip('0') if e.date else ''
        out.append({'label': e.title,
                    'sub': ' · '.join(x for x in (when, e.location) if x) or 'Event',
                    'url': '/calendar/'})
    return out


def projects(q, user):
    from content.models import Project
    rows = Project.objects.filter(
        Q(title__icontains=q) | Q(location__icontains=q) | Q(type__icontains=q)
    )[:PER_SOURCE]
    from urllib.parse import quote
    return [{'label': p.title, 'sub': p.location or p.type or 'Project',
             'url': '/portfolio/?q=' + quote(p.title)} for p in rows]


def _doc_allowed(obj, user, groups):
    """Whether a policy, library or brand tile is open to this reader.

    `restricted` means the object carries its own list of groups; everything
    else is open to anybody signed in.
    """
    if getattr(obj, 'access', 'everyone') != 'restricted':
        return True
    if is_intranet_admin(user):
        return True
    try:
        return obj.allowed_groups.filter(pk__in=[g.pk for g in groups]).exists()
    except Exception:                                            # noqa: BLE001
        return False


def policies(q, user):
    from portal.models import Policy
    groups = list(user.groups.all())
    rows = (Policy.objects.filter(Q(title__icontains=q) | Q(description__icontains=q))
            .select_related('category').prefetch_related('allowed_groups'))
    out = []
    for p in rows:
        category = p.category if p.category_id else None
        if not _doc_allowed(p, user, groups):
            continue
        if category is not None and not _doc_allowed(category, user, groups):
            continue
        out.append({'label': p.title,
                    'sub': category.name if category else 'Policy',
                    'url': '/policies/'})
        if len(out) >= PER_SOURCE:
            break
    return out


def brand_files(q, user):
    from portal.models import BrandTemplate
    groups = list(user.groups.all())
    rows = (BrandTemplate.objects
            .filter(Q(name__icontains=q) | Q(description__icontains=q))
            .select_related('section').prefetch_related('allowed_groups'))
    out = [{'label': t.name,
            'sub': t.section.name if t.section_id else 'Brand Files',
            'url': '/brand/'}
           for t in rows if _doc_allowed(t, user, groups)][:PER_SOURCE]
    return out


def communities(q, user):
    from exchange.models import Community
    rows = Community.objects.filter(Q(name__icontains=q) | Q(description__icontains=q))
    return [{'label': c.name,
             'sub': f'{c.member_count} member{"" if c.member_count == 1 else "s"}',
             'url': f'/exchange/{c.slug}/'}
            for c in rows if c.can_read(user)][:PER_SOURCE]


def threads(q, user):
    """Threads, and the replies in them — but only where the reader may look."""
    from exchange.models import Community, Thread
    rows = Thread.objects.filter(
        Q(title__icontains=q) | Q(body__icontains=q)).select_related('community')
    if not is_intranet_admin(user):
        rows = rows.filter(Q(community__visibility=Community.Visibility.PUBLIC)
                           | Q(community__members=user)
                           | Q(community__created_by=user))
    return [{'label': t.title, 'sub': t.community.name,
             'url': f'/exchange/{t.community.slug}/{t.pk}/'}
            for t in rows.distinct()[:PER_SOURCE]]


def services(q, user):
    """My AJi services and the categories they sit in."""
    from services.models import Service, ServiceCategory
    out = []
    for s in (Service.objects.filter(
            Q(name__icontains=q) | Q(description__icontains=q), is_active=True)
            .select_related('category', 'group')[:PER_SOURCE]):
        if s.group_id and not (is_intranet_admin(user)
                               or user.groups.filter(pk=s.group_id).exists()):
            continue
        out.append({'label': s.name,
                    'sub': s.category.name if s.category_id else 'Service',
                    'url': f'/my-aji/service/{s.pk}/'})
    for c in ServiceCategory.objects.filter(name__icontains=q, is_active=True)[:2]:
        out.append({'label': c.name, 'sub': 'Service category',
                    'url': f'/my-aji/category/{c.pk}/'})
    return out[:PER_SOURCE]


def menu(q, user):
    from cafeteria.models import MenuItem
    rows = MenuItem.objects.filter(
        Q(name__icontains=q) | Q(description__icontains=q), is_active=True
    ).select_related('category')[:PER_SOURCE]
    return [{'label': m.name,
             'sub': m.category.name if m.category_id else 'Cafeteria',
             'url': '/cafeteria/'} for m in rows]


def skills(q, user):
    from people.models import Skill
    rows = Skill.objects.filter(name__icontains=q).select_related('category')[:PER_SOURCE]
    return [{'label': s.name,
             'sub': s.category.name if s.category_id else 'Skill',
             'url': f'/people/skills/?skill={s.pk}'} for s in rows]


def departments(q, user):
    from people.models import Department, Entity
    from urllib.parse import quote
    out = [{'label': d.name, 'sub': 'Department',
            'url': '/people/?q=' + quote(d.name)}
           for d in Department.objects.filter(name__icontains=q)[:2]]
    out += [{'label': e.name, 'sub': f'Entity · {e.code}',
             'url': f'/people/?entity={e.pk}'}
            for e in Entity.objects.filter(
                Q(name__icontains=q) | Q(code__icontains=q))[:2]]
    return out[:PER_SOURCE]


def handbook(q, user):
    """The employee handbook, chapter by chapter."""
    from portal.models import OnboardingItem
    rows = (OnboardingItem.objects.filter(
        Q(label__icontains=q) | Q(content__icontains=q))
        .select_related('section')[:PER_SOURCE])
    return [{'label': i.label,
             'sub': (i.section.title if i.section_id else '') or 'Handbook',
             'url': '/onboarding/'} for i in rows]


def wellness(q, user):
    from portal.models import ChildcarePartner, WellnessProgram
    out = [{'label': w.name, 'sub': w.period or 'Wellness',
            'url': '/people-culture/'}
           for w in WellnessProgram.objects.filter(
               Q(name__icontains=q) | Q(description__icontains=q))[:2]]
    out += [{'label': c.name, 'sub': 'Childcare partner',
             'url': '/people-culture/'}
            for c in ChildcarePartner.objects.filter(name__icontains=q)[:2]]
    return out[:PER_SOURCE]


def albums(q, user):
    from portal.models import BoothAlbum
    rows = BoothAlbum.objects.filter(
        Q(name__icontains=q) | Q(tag__icontains=q) | Q(period__icontains=q)
    )[:PER_SOURCE]
    return [{'label': a.name, 'sub': a.period or 'AJi Booth',
             'url': f'/booth/album/{a.pk}/'} for a in rows]


def rooms(q, user):
    from services.models import MeetingRoom
    rows = MeetingRoom.objects.filter(name__icontains=q, is_active=True)[:2]
    return [{'label': r.name,
             'sub': f'{r.chairs} chairs' if r.chairs else 'Meeting room',
             'url': '/services/room-booking/'} for r in rows]


def recognition(q, user):
    from taqdeer.models import Certification
    rows = (Certification.objects.filter(
        Q(name__icontains=q) | Q(issuer__icontains=q), published=True)
        .select_related('employee')[:PER_SOURCE])
    return [{'label': c.name,
             'sub': (c.employee.get_full_name() or c.employee.username
                     if c.employee_id else 'Certification'),
             'url': '/taqdeer/'} for c in rows]


#: type key, label shown as the row's tag, and the function. The key is what
#: the front end maps to an icon.
PROVIDERS = [
    ('place', destinations),
    ('person', people),
    ('page', pages),
    ('news', news),
    ('hr', hr_updates),
    ('policy', policies),
    ('document', brand_files),
    ('project', projects),
    ('service', services),
    ('community', communities),
    ('thread', threads),
    ('skill', skills),
    ('team', departments),
    ('handbook', handbook),
    ('event', events),
    ('menu', menu),
    ('room', rooms),
    ('wellness', wellness),
    ('album', albums),
    ('recognition', recognition),
]

#: Named here so it is obvious what is *not* searched, and so somebody adding a
#: provider has to think about whether it belongs on this list instead.
NEVER_SEARCHED = (
    'chat messages and group chats', 'feedback submissions',
    'anonymous suggestions', 'training requests', 'notifications',
    'the audit log', 'backups', 'private calendar entries', 'draft pages',
)


def _fuzzy(q, user):
    """What somebody probably meant, when nothing matched what they typed.

    "raqder" is Taqdeer with two fingers off — and substring matching returns
    absolutely nothing for it, which reads as "the Hub has no such thing"
    rather than "check your spelling".

    Compared word by word rather than against whole labels: "raqder" against
    "AJi Taqdeer" scores too low to pass any useful cutoff, while "raqder"
    against the word "taqdeer" is a clear match. Only places, pages, communities
    and skills are candidates — a name is not something to guess at, and
    offering the wrong colleague is worse than offering nobody.
    """
    import difflib

    candidates = {}                      # word -> row
    for row in _visible_destinations(user):
        for word in row['label'].lower().replace('&', ' ').split():
            if len(word) > 3:
                candidates.setdefault(word, row)

    from cms.models import Page
    for page in Page.objects.filter(is_published=True, draft_of__isnull=True):
        if page.is_archived or page.is_scheduled or not page.is_visible_to(user):
            continue
        row = {'label': page.title, 'sub': page.eyebrow or 'Page',
               'url': page.bound_path or f'/p/{page.slug}/'}
        for word in page.title.lower().split():
            if len(word) > 3:
                candidates.setdefault(word, row)

    from people.models import Skill
    for skill in Skill.objects.all():
        row = {'label': skill.name, 'sub': 'Skill',
               'url': f'/people/skills/?skill={skill.pk}'}
        for word in skill.name.lower().split():
            if len(word) > 3:
                candidates.setdefault(word, row)

    hits, seen = [], set()
    for word in difflib.get_close_matches(q.lower(), candidates.keys(),
                                          n=6, cutoff=0.7):
        row = dict(candidates[word])
        if row['url'] in seen:
            continue
        seen.add(row['url'])
        row['type'] = 'place'
        # difflib returns these best-first. Score them so the sort in run()
        # keeps that order — otherwise the panel says "showing X" and then puts
        # something else at the top, which is worse than not guessing at all.
        row['score'] = 90 - len(hits)
        hits.append(row)
    return hits[:5]


def run(q, user):
    """Every source, ranked, capped. One provider failing costs its own rows.

    Returns (rows, suggestion): `suggestion` is the word we guessed at when
    nothing matched, so the panel can say what it did rather than silently
    answering a different question.
    """
    q = (q or '').strip()
    if len(q) < MIN_QUERY:
        return [], ''

    rows = []
    for kind, provider in PROVIDERS:
        try:
            for row in provider(q, user) or []:
                row['type'] = kind
                # A provider may score its own row — the people source does,
                # because a query that matched an extension is nowhere in the
                # label and would otherwise rank last.
                row.setdefault('score', _score(row['label'], q))
                rows.append(row)
        except Exception:                                        # noqa: BLE001
            import logging
            logging.getLogger(__name__).exception(
                '[search] %s failed for %r', kind, q)
    suggestion = ''
    if not rows:
        rows = _fuzzy(q, user)
        suggestion = rows[0]['label'] if rows else ''

    # Stable: score first, then the order PROVIDERS declares, so equal matches
    # come back in the same order every time rather than shuffling per query.
    order = {kind: i for i, (kind, _fn) in enumerate(PROVIDERS)}
    rows.sort(key=lambda r: (-r.get('score', 50), order.get(r['type'], 99),
                             r['label']))
    for row in rows:
        row.pop('score', None)
    return rows[:TOTAL], suggestion
