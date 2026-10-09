# cms/views.py
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify

from .models import (Audience, Page, Section, SectionItem, SectionType,
                     user_can_edit_cms, user_can_publish_cms)


def wants_fragment(request):
    """True when the fragment-swap script sent this request.

    The management screens (this file) are the last part of the CMS that still
    worked as a form post followed by a real Django redirect -- every click
    between them was a full browser navigation. The script in
    includes/cms_fragment_nav.html marks every request it makes with this
    header, so a POST handler can answer with JSON {redirect: ...} instead of
    an HTTP redirect, and the script fetches that URL itself and swaps only
    #hubMain in. Anyone without JS still gets the classic redirect: the header
    is simply absent, so this returns False and every view falls back to
    exactly what it did before.
    """
    return request.headers.get('X-Cms-Fragment') == '1'


def _saved(request, message, view_name, **kwargs):
    """After a successful save: JSON for the fragment script, or the classic
    messages-then-redirect for anyone without JS.

    Not used for the embedded page-settings form, which has to preserve its own
    query string (?embed=1) — see page_settings for that one.
    """
    url = reverse(view_name, kwargs=kwargs) if kwargs else reverse(view_name)
    if wants_fragment(request):
        return JsonResponse({'ok': True, 'redirect': url, 'message': message})
    if message:
        messages.success(request, message)
    return redirect(url)


def cms_editor_required(view):
    """Gate the management screens. Roles are assigned later by group; the
    check itself lives in one place so it never drifts between views."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not user_can_edit_cms(request.user):
            messages.error(request, "You don't have permission to manage content.")
            return redirect('home')
        return view(request, *args, **kwargs)
    return wrapper


# ── public ────────────────────────────────────────────────────────────────

@login_required
def page_index(request):
    """Every page the Hub manages, for an editor; the published ones for a reader.

    This listed only pages flagged show_in_nav — and nothing was, because binding
    a route does not set it. So a screen whose whole job is to show what the CMS
    manages showed "No pages published yet" while managing eighteen of them.
    """
    can_edit = user_can_edit_cms(request.user)

    if not can_edit:
        # A reader gets the directory it always was: standalone pages somebody
        # deliberately listed.
        pages = [p for p in Page.objects.filter(show_in_nav=True).live()
                 if p.is_visible_to(request.user)]
        return render(request, 'cms/page_index.html', {
            'reader_pages': pages, 'can_edit': False, 'can_publish': False,
        })

    from .nav_views import nav_item_for
    rows = []
    for page in (Page.objects.filter(draft_of__isnull=True)
                 .prefetch_related('sections', 'audience_groups')
                 .order_by('title')):
        item = nav_item_for(page)
        rows.append({
            'page': page,
            'in_nav': item is not None,
            'nav_item': item,
            'blocks': page.sections.count(),
            'has_draft': page.drafts.exists(),
        })

    return render(request, 'cms/page_index.html', {
        'rows': rows,
        'in_nav': [r for r in rows if r['in_nav']],
        'off_nav': [r for r in rows if not r['in_nav']],
        'can_edit': True,
        # The navigation is offered only to publishers: it is the quickest way
        # to make the whole Hub look broken.
        'can_publish': user_can_publish_cms(request.user),
    })


@cms_editor_required
def bind_path(request):
    """Start managing an existing route by creating a Page bound to it.

    This is what makes the edit toolbar useful on hardcoded pages: the route
    keeps its own template and view, and the CMS simply contributes sections
    rendered wherever that template places the {% cms_sections %} tag.
    """
    if request.method != 'POST':
        return redirect('cms_index')

    path = (request.POST.get('path') or '').strip()
    if not path.startswith('/'):
        messages.error(request, 'That does not look like a Hub route.')
        return redirect('cms_index')

    existing = Page.for_path(path)
    if existing:
        return redirect('cms_page_manage', slug=existing.slug)

    # Name it after the route so the pages index stays readable.
    label = path.strip('/').replace('-', ' ').replace('/', ' · ').title() or 'Home'
    page = Page(
        title=label,
        slug=_unique_slug(Page, slugify(path.strip('/')) or 'page'),
        bound_path=path,
        # Bound pages are published immediately — the host route already
        # controls who may reach it, and a draft would hide the sections.
        is_published=True,
        updated_by=request.user,
    )
    page.save()
    messages.success(request, f'“{label}” is now managed. Add your first section.')
    return redirect('cms_page_manage', slug=page.slug)


@login_required
def page_detail(request, slug):
    page = get_object_or_404(Page, slug=slug)
    # A bound page belongs to its host route; serving it here too would show
    # the same sections at two URLs.
    if page.bound_path:
        return redirect(page.bound_path)
    if not page.is_visible_to(request.user):
        raise Http404
    return render(request, 'cms/page_detail.html', {
        'page': page,
        'sections': page.visible_sections(request.user),
        'can_edit': user_can_edit_cms(request.user),
    })


# ── management ────────────────────────────────────────────────────────────

@cms_editor_required
def page_manage(request, slug):
    page = get_object_or_404(Page, slug=slug)
    return render(request, 'cms/page_manage.html', {
        'page': page,
        'sections': page.sections.prefetch_related('items'),
        # Removed blocks are kept so a delete can be undone. Listed here so they
        # are recoverable after the editing session that removed them has ended,
        # not only while the Undo button is on screen.
        'removed': Section.all_objects.filter(page=page, deleted_at__isnull=False)
                          .order_by('-deleted_at'),
        'section_types': SectionType.choices,
        'can_publish': user_can_publish_cms(request.user),
    })


@cms_editor_required
def page_settings(request, slug=None):
    """Create or edit the page shell (title, audience, scheduling)."""
    # Rendered without the site around it when the editing panel asks for it.
    embed = request.GET.get('embed') == '1'
    page = get_object_or_404(Page, slug=slug) if slug else None

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        if not title:
            if wants_fragment(request):
                return JsonResponse({'error': 'The page needs a title.'}, status=400)
            messages.error(request, 'The page needs a title.')
        else:
            if page is None:
                page = Page()
            page.title = title
            page.eyebrow = request.POST.get('eyebrow', '').strip()
            page.subtitle = request.POST.get('subtitle', '').strip()
            page.audience = request.POST.get('audience') or Audience.EVERYONE
            page.show_in_nav = request.POST.get('show_in_nav') == 'on'
            page.nav_order = _as_int(request.POST.get('nav_order'), 0)

            wanted = slugify(request.POST.get('slug', '') or title)
            page.slug = _unique_slug(Page, wanted, page)

            if user_can_publish_cms(request.user):
                page.is_published = request.POST.get('is_published') == 'on'
                page.publish_at = request.POST.get('publish_at') or None
                page.archive_at = request.POST.get('archive_at') or None

            page.updated_by = request.user
            page.save()
            page.audience_groups.set(request.POST.getlist('audience_groups'))

            # An embedded save stays embedded. This used to send even the
            # embedded form on to the full management screen — nav, chat bubble
            # and all — rendered inside the editing panel's 320px iframe. The
            # embedded template extends _bare.html, not base.html, so it never
            # carries the fragment-swap script and there is nothing to detect
            # here — a classic redirect is the only path that applies.
            # ?saved=1 is how the embedded template knows to flash the parent
            # window's toast and refresh its header, since the iframe itself
            # never renders Django's messages.
            if embed:
                return redirect(reverse('cms_page_settings', kwargs={'slug': page.slug})
                                + '?embed=1&saved=1')
            return _saved(request, 'Page saved.', 'cms_page_manage', slug=page.slug)

    from django.contrib.auth.models import Group
    return render(request, 'cms/page_settings.html', {
        'embed': embed,
        'page': page,
        'audiences': Audience.choices,
        'groups': Group.objects.all(),
        'can_publish': user_can_publish_cms(request.user),
    })


@cms_editor_required
def section_form(request, slug, pk=None):
    page = get_object_or_404(Page, slug=slug)
    section = get_object_or_404(Section, pk=pk, page=page) if pk else None

    if request.method == 'POST':
        if section is None:
            section = Section(page=page)
            section.order = (page.sections.count() + 1) * 10
        section.type = request.POST.get('type') or SectionType.RICH_TEXT
        section.title = request.POST.get('title', '').strip()
        section.subtitle = request.POST.get('subtitle', '').strip()
        section.body = request.POST.get('body', '')
        section.icon = request.POST.get('icon', '').strip()
        section.audience = request.POST.get('audience') or Audience.EVERYONE
        section.is_visible = request.POST.get('is_visible') == 'on'
        section.pinned = request.POST.get('pinned') == 'on'
        section.publish_at = request.POST.get('publish_at') or None
        section.archive_at = request.POST.get('archive_at') or None

        if request.POST.get('remove_image') == 'on' and section.image:
            section.image.delete(save=False)
            section.image = None
        if request.FILES.get('image'):
            section.image = request.FILES['image']

        # Merge rather than replace. This form only knows about three settings,
        # so assigning a fresh dict silently destroyed every other key — saving a
        # feature block through it wiped the `widget` key that decides what the
        # block renders, leaving a blank space on the page.
        cfg = dict(section.config or {})
        cfg.update({
            'columns': _as_int(request.POST.get('columns'), 0),
            'limit':   _as_int(request.POST.get('limit'), 0),
            'embed':   request.POST.get('embed', '').strip(),
        })
        section.config = cfg
        # Raw HTML is only trusted when a superuser saved it; anyone else
        # editing the block drops it back to escaped until re-approved.
        if section.type == SectionType.CUSTOM_HTML:
            section.html_approved = bool(request.user.is_superuser)
        section.save()
        section.audience_groups.set(request.POST.getlist('audience_groups'))
        return _saved(request, 'Section saved.', 'cms_section_edit',
                     slug=page.slug, pk=section.pk)

    from django.contrib.auth.models import Group
    return render(request, 'cms/section_form.html', {
        'page': page,
        'section': section,
        'section_types': SectionType.choices,
        'audiences': Audience.choices,
        'groups': Group.objects.all(),
        'items': section.items.all() if section else [],
    })


@cms_editor_required
def section_action(request, slug, pk, action):
    page = get_object_or_404(Page, slug=slug)
    section = get_object_or_404(Section, pk=pk, page=page)

    if request.method != 'POST':
        return redirect('cms_page_manage', slug=page.slug)

    message = ''
    if action == 'delete':
        section.delete()
        message = 'Section removed.'
    elif action == 'toggle':
        section.is_visible = not section.is_visible
        section.save(update_fields=['is_visible'])
        message = 'Section shown.' if section.is_visible else 'Section hidden.'
    elif action == 'pin':
        section.pinned = not section.pinned
        section.save(update_fields=['pinned'])
        message = 'Section pinned.' if section.pinned else 'Section unpinned.'
    elif action == 'duplicate':
        items = list(section.items.all())
        groups = list(section.audience_groups.all())
        section.pk = None
        section.title = f'{section.title} (copy)'.strip()
        section.order += 1
        section.is_visible = False          # a copy starts hidden, never live by surprise
        section.save()
        section.audience_groups.set(groups)
        for it in items:
            it.pk = None
            it.section = section
            it.save()
        message = 'Section duplicated — the copy is hidden until you show it.'

    return _saved(request, message, 'cms_page_manage', slug=page.slug)


@cms_editor_required
def sections_reorder(request, slug):
    """Persist a new drag-and-drop order. Expects ids[]=<pk> in page order."""
    page = get_object_or_404(Page, slug=slug)
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    ids = request.POST.getlist('ids[]') or request.POST.getlist('ids')
    known = {str(s.pk): s for s in page.sections.all()}
    for index, raw in enumerate(ids):
        section = known.get(str(raw))
        if section:
            section.order = (index + 1) * 10
            section.save(update_fields=['order'])
    return JsonResponse({'ok': True, 'count': len(ids)})


# ── section items ─────────────────────────────────────────────────────────

@cms_editor_required
def item_form(request, slug, section_pk, pk=None):
    page = get_object_or_404(Page, slug=slug)
    section = get_object_or_404(Section, pk=section_pk, page=page)
    item = get_object_or_404(SectionItem, pk=pk, section=section) if pk else None

    if request.method == 'POST':
        if item is None:
            item = SectionItem(section=section)
            item.order = (section.items.count() + 1) * 10
        item.title = request.POST.get('title', '').strip()
        item.text = request.POST.get('text', '')
        item.meta = request.POST.get('meta', '').strip()
        item.badge = request.POST.get('badge', '').strip()
        item.icon = request.POST.get('icon', '').strip()
        item.url = request.POST.get('url', '').strip()
        item.button_label = request.POST.get('button_label', '').strip()
        item.button_icon = request.POST.get('button_icon', '').strip()
        item.is_visible = request.POST.get('is_visible') == 'on'

        if request.POST.get('remove_image') == 'on' and item.image:
            item.image.delete(save=False)
            item.image = None
        if request.FILES.get('image'):
            item.image = request.FILES['image']
        if request.POST.get('remove_file') == 'on' and item.file:
            item.file.delete(save=False)
            item.file = None
        if request.FILES.get('file'):
            item.file = request.FILES['file']

        item.save()
        return _saved(request, 'Item saved.', 'cms_section_edit',
                     slug=page.slug, pk=section.pk)

    return render(request, 'cms/item_form.html', {
        'page': page, 'section': section, 'item': item,
    })


@cms_editor_required
def item_action(request, slug, section_pk, pk, action):
    page = get_object_or_404(Page, slug=slug)
    section = get_object_or_404(Section, pk=section_pk, page=page)
    item = get_object_or_404(SectionItem, pk=pk, section=section)

    message = ''
    if request.method == 'POST':
        if action == 'delete':
            item.delete()
            message = 'Item removed.'
        elif action == 'toggle':
            item.is_visible = not item.is_visible
            item.save(update_fields=['is_visible'])
        elif action in ('up', 'down'):
            _nudge(section, item, -1 if action == 'up' else 1)

    return _saved(request, message, 'cms_section_edit', slug=page.slug, pk=section.pk)


# ── helpers ───────────────────────────────────────────────────────────────

def _as_int(raw, default=0):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _unique_slug(model, base, instance=None):
    base = base or 'page'
    slug, n = base, 2
    while True:
        clash = model.objects.filter(slug=slug)
        if instance is not None and instance.pk:
            clash = clash.exclude(pk=instance.pk)
        if not clash.exists():
            return slug
        slug = f'{base}-{n}'
        n += 1


def _nudge(section, item, delta):
    """Swap an item with its neighbour, then renumber so orders stay tidy."""
    ordered = list(section.items.all())
    idx = next((i for i, o in enumerate(ordered) if o.pk == item.pk), None)
    if idx is None:
        return
    target = idx + delta
    if not (0 <= target < len(ordered)):
        return
    ordered[idx], ordered[target] = ordered[target], ordered[idx]
    for i, o in enumerate(ordered):
        o.order = (i + 1) * 10
        o.save(update_fields=['order'])
