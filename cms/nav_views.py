# cms/nav_views.py
"""Managing the navigation and the site logo.

The menu was a JavaScript array in base.html, so renaming "Policies & SOPs" or
adding a link meant a template edit and a deploy. These turn it into records:
rename, relink, re-icon, reorder, hide, add, remove — and one level of dropdown,
which is all the header can draw.

Everything is deliberately narrow. A navigation edit is the easiest way to make
the whole Hub look broken, so:

* only somebody who can publish may change it, not every content editor;
* a link must be a Hub route or a full external address, so a typo cannot become
  a relative URL that resolves differently on every page;
* removing an item that has children takes the children with it, and says so.
"""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .models import Audience, NavItem, SiteBranding, user_can_publish_cms


def _denied(request):
    messages.error(request, "You don't have permission to change the navigation.")
    return redirect('home')


def _clean_url(raw):
    """A Hub route or a full address, or '' — never anything ambiguous.

    A bare "policies" would resolve against whatever page the reader happened to
    be on, so it is rejected rather than quietly turned into something that works
    on the homepage and 404s everywhere else.
    """
    url = (raw or '').strip()
    if not url:
        return '', None
    if url.startswith(('http://', 'https://', 'mailto:', 'tel:')):
        return url, None
    if url.startswith('/'):
        return url, None
    return '', ('A link must start with / for a page on the Hub, or with '
                'https:// for somewhere else.')


def nav_manage(request):
    """The navigation and branding screen."""
    if not user_can_publish_cms(request.user):
        return _denied(request)

    from django.contrib.auth.models import Group
    tops = (NavItem.objects.filter(parent__isnull=True)
            .prefetch_related('children', 'audience_groups'))
    return render(request, 'cms/nav_manage.html', {
        'tops': tops,
        'audiences': Audience.choices,
        'groups': Group.objects.all(),
        'branding': SiteBranding.get(),
        # So an editor can see what a reader with no special access would get,
        # which is the thing most easily got wrong here.
        'public_count': sum(1 for i in tops if i.audience == Audience.EVERYONE),
    })


@require_POST
def nav_save(request, pk=None):
    """Create or update one entry."""
    if not user_can_publish_cms(request.user):
        return JsonResponse({'error': 'Not allowed.'}, status=403)

    item = get_object_or_404(NavItem, pk=pk) if pk else NavItem()

    label = (request.POST.get('label') or '').strip()
    if not label:
        return JsonResponse({'error': 'Give it a label.'}, status=400)

    url, problem = _clean_url(request.POST.get('url'))
    if problem:
        return JsonResponse({'error': problem}, status=400)

    parent_raw = (request.POST.get('parent') or '').strip()
    parent = None
    if parent_raw:
        parent = NavItem.objects.filter(pk=parent_raw).first()
        if parent is None:
            return JsonResponse({'error': 'That menu no longer exists.'}, status=400)

    item.label = label[:60]
    item.url = url[:300]
    item.icon = (request.POST.get('icon') or '').strip()[:40]
    item.parent = parent
    item.is_visible = request.POST.get('is_visible') == 'on'
    item.new_tab = request.POST.get('new_tab') == 'on'
    if request.POST.get('audience') in dict(Audience.choices):
        item.audience = request.POST['audience']
    if not item.pk:
        last = NavItem.objects.filter(parent=parent).order_by('-order').first()
        item.order = (last.order if last else 0) + 10

    try:
        item.full_clean(exclude=['audience_groups'])
    except ValidationError as exc:
        return JsonResponse({'error': '; '.join(
            m for msgs in exc.message_dict.values() for m in msgs)}, status=400)

    item.save()
    item.audience_groups.set(request.POST.getlist('audience_groups'))
    messages.success(request, f'“{item.label}” saved.')
    return JsonResponse({'ok': True, 'pk': item.pk})


@require_POST
def nav_delete(request, pk):
    if not user_can_publish_cms(request.user):
        return JsonResponse({'error': 'Not allowed.'}, status=403)
    item = get_object_or_404(NavItem, pk=pk)
    kids = item.children.count()
    label = item.label
    item.delete()          # cascades, which is why the count is reported back
    messages.success(request, f'“{label}” removed.'
                     + (f' {kids} link(s) inside it went too.' if kids else ''))
    return JsonResponse({'ok': True, 'children_removed': kids})


@require_POST
def nav_reorder(request):
    """Persist a new order. Expects ids[] in the order they should appear."""
    if not user_can_publish_cms(request.user):
        return JsonResponse({'error': 'Not allowed.'}, status=403)
    ids = request.POST.getlist('ids[]') or request.POST.getlist('ids')
    known = {str(i.pk): i for i in NavItem.objects.all()}
    n = 0
    for index, raw in enumerate(ids):
        item = known.get(str(raw))
        if item:
            item.order = (index + 1) * 10
            item.save(update_fields=['order'])
            n += 1
    return JsonResponse({'ok': True, 'count': n})


def _wants_json(request):
    """True when a script sent this, rather than the browser submitting a form.

    A JsonResponse handed to a plain form post is simply *displayed* — which is
    what happened when the logo saved correctly and the screen filled with
    {"ok": true, ...}. So a real form post gets a redirect instead.
    """
    return (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
            or 'application/json' in (request.headers.get('Accept') or ''))


@require_POST
def branding_save(request):
    """The logo, the tab icon and the site name."""
    if not user_can_publish_cms(request.user):
        if _wants_json(request):
            return JsonResponse({'error': 'Not allowed.'}, status=403)
        return _denied(request)

    b = SiteBranding.get()
    name = (request.POST.get('site_name') or '').strip()
    if name:
        b.site_name = name[:80]

    for field in ('logo', 'favicon'):
        if request.POST.get(f'remove_{field}') == '1':
            setattr(b, field, None)
            continue
        upload = request.FILES.get(field)
        if not upload:
            continue
        name_l = (upload.name or '').lower()
        if not name_l.endswith(('.png', '.jpg', '.jpeg', '.webp', '.svg', '.gif', '.ico')):
            return _branding_error(
                request, 'Use a PNG, JPG, WEBP, SVG, GIF or ICO file.')
        if upload.size > 4 * 1024 * 1024:
            return _branding_error(
                request, 'That image is over 4 MB. The logo is drawn small, so a '
                         'smaller file will look the same and load faster.')
        setattr(b, field, upload)

    b.save()
    messages.success(request, 'Branding saved.')
    if _wants_json(request):
        return JsonResponse({
            'ok': True,
            'logo': b.logo.url if b.logo else '',
            'favicon': b.favicon.url if b.favicon else '',
        })
    # A plain form post goes back to the screen it came from, where the new logo
    # is already in the header.
    return redirect('cms_nav')


def _branding_error(request, message):
    if _wants_json(request):
        return JsonResponse({'error': message}, status=400)
    messages.error(request, message)
    return redirect('cms_nav')


# ── putting a page in the menu ────────────────────────────────────────────

def nav_item_for(page):
    """The navigation entry pointing at this page, if there is one."""
    url = page.get_absolute_url()
    if not url:
        return None
    # Tolerate a stored link written with or without its trailing slash.
    candidates = {url, url.rstrip('/') + '/', url.rstrip('/')}
    return NavItem.objects.filter(url__in=[u for u in candidates if u]).first()


@require_POST
def page_nav_toggle(request, slug):
    """Add this page to the top navigation, or take it out again.

    The pages index used to list only pages flagged show_in_nav, which nothing
    was — so a screen meant to show what the CMS manages showed nothing, and
    there was no way to get a page into the menu without going and typing its
    URL by hand on the navigation screen.
    """
    from .models import Page

    if not user_can_publish_cms(request.user):
        return JsonResponse({'error': 'Changing the menu needs a publisher.'},
                            status=403)

    page = get_object_or_404(Page, slug=slug, draft_of__isnull=True)
    existing = nav_item_for(page)

    if existing is not None:
        label = existing.label
        kids = existing.children.count()
        existing.delete()
        messages.success(request, f'“{label}” removed from the menu.')
        return JsonResponse({'ok': True, 'in_nav': False,
                             'children_removed': kids})

    url = page.get_absolute_url()
    if not url:
        return JsonResponse(
            {'error': 'This page has no address to link to yet.'}, status=400)

    last = NavItem.objects.filter(parent__isnull=True).order_by('-order').first()
    item = NavItem.objects.create(
        label=page.title[:60],
        url=url[:300],
        order=(last.order if last else 0) + 10,
        # The menu entry starts as visible to whoever the page is visible to, so
        # adding a link cannot quietly widen who can see something.
        audience=page.audience,
    )
    item.audience_groups.set(page.audience_groups.all())
    messages.success(request, f'“{item.label}” added to the menu.')
    return JsonResponse({'ok': True, 'in_nav': True, 'pk': item.pk,
                         'label': item.label})
