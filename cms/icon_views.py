# cms/icon_views.py
"""The icon library: browse, edit, upload, reset.

Icons render inline on every page including the navigation, so two rules hold
throughout: uploaded SVG is sanitised to shape elements before it is stored, and
a built-in icon can be edited or reset but never deleted, because templates refer
to it by name and deleting one would silently blank a button.
"""
from django.contrib import messages
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import phosphor
from .icon_models import sanitise_svg, shipped_shape
from .models import Icon, IconCategory, user_can_manage_icons


def _denied(request):
    messages.error(request, "You don't have permission to manage icons.")
    return redirect('home')


def icon_library(request):
    """Every icon, grouped, with the picker and upload form."""
    if not user_can_manage_icons(request.user):
        return _denied(request)

    q = (request.GET.get('q') or '').strip()
    icons = Icon.objects.all()
    if q:
        icons = icons.filter(name__icontains=q)

    groups = []
    for value, label in IconCategory.choices:
        rows = [i for i in icons if i.category == value]
        if rows:
            groups.append({'value': value, 'label': label, 'icons': rows})

    return render(request, 'cms/icon_library.html', {
        'groups': groups,
        'q': q,
        'total': Icon.objects.count(),
        'customised': Icon.objects.exclude(body='').count(),
        'categories': IconCategory.choices,
        'set': phosphor.version(),
    })


@require_POST
def icon_save(request, pk=None):
    """Create or update one icon. Returns JSON so the library updates in place."""
    if not user_can_manage_icons(request.user):
        return JsonResponse({'error': "You don't have permission to manage icons."},
                            status=403)

    icon = get_object_or_404(Icon, pk=pk) if pk else Icon(category=IconCategory.CUSTOM)

    name = (request.POST.get('name') or '').strip()
    if icon.pk and icon.is_builtin:
        # Renaming a built-in would break every template that refers to it.
        name = icon.name
    if not name:
        return JsonResponse({'error': 'Give the icon a name.'}, status=400)

    clash = Icon.objects.filter(name=name).exclude(pk=icon.pk).exists()
    if clash:
        return JsonResponse({'error': f'An icon called "{name}" already exists.'},
                            status=400)

    body = request.POST.get('body') or ''
    upload = request.FILES.get('file')
    picture = None
    if upload:
        name_l = (upload.name or '').lower()
        is_svg = name_l.endswith('.svg') or 'svg' in (upload.content_type or '')

        if is_svg:
            if upload.size > 512 * 1024:
                return JsonResponse(
                    {'error': 'That SVG is over 512 KB, which is very large for an '
                              'icon. Try exporting it with fewer points.'}, status=400)
            try:
                body = upload.read().decode('utf-8', 'ignore')
            except Exception:
                return JsonResponse({'error': 'That file could not be read as SVG.'},
                                    status=400)
        else:
            # A picture icon: PNG, JPG, WEBP or GIF. Kept as a file and drawn in
            # the same box as a drawn icon, because an icon is not always line
            # art — a logo or a partner mark is a perfectly good icon, and
            # accepting only SVG meant those could not be used at all.
            if not name_l.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif')):
                return JsonResponse(
                    {'error': 'Use an SVG, PNG, JPG, WEBP or GIF file.'}, status=400)
            if upload.size > 4 * 1024 * 1024:
                return JsonResponse(
                    {'error': 'That image is over 4 MB. Icons are drawn small, so a '
                              'smaller file will look the same and load faster.'},
                    status=400)
            picture = upload

    # Sanitise here only to check something usable survives. The raw markup is
    # what gets assigned: Icon.save() reads the coordinate grid and the paint
    # style off the <svg> wrapper before stripping it, and sanitising first threw
    # that away — which is why an uploaded icon drawn on a 512 grid was rendered
    # into a 24-unit box and appeared blank.
    if picture is None and body.strip() and not sanitise_svg(body):
        return JsonResponse(
            {'error': 'Nothing usable was found in that SVG. Icons need path, '
                      'circle, rect, line, polyline or polygon shapes.'}, status=400)

    icon.name = name
    icon.label = (request.POST.get('label') or '').strip()
    if picture is not None:
        # A picture replaces any drawn shape, so the two never fight over which
        # one renders.
        icon.image = picture
        icon.body = ''
    else:
        icon.body = body
        if request.POST.get('remove_image') == '1' and icon.image:
            icon.image = None
    if request.POST.get('category') in dict(IconCategory.choices) and not icon.is_builtin:
        icon.category = request.POST['category']
    try:
        icon.stroke = float(request.POST.get('stroke') or icon.stroke or 2)
    except (TypeError, ValueError):
        pass
    icon.filled = str(request.POST.get('filled', '')).lower() in ('1', 'true', 'on', 'yes')
    if not icon.pk:
        icon.uploaded_by = request.user
    icon.save()

    from .models import render_icon
    return JsonResponse({
        'ok': True,
        'message': f'“{icon}” saved.',
        'pk': icon.pk,
        'name': icon.name,
        'svg': render_icon(icon.name, size=26),
        'customised': icon.is_overridden or not icon.is_builtin,
    })


@require_POST
def icon_reset(request, pk):
    """Drop a customisation and go back to the shipped shape."""
    if not user_can_manage_icons(request.user):
        return JsonResponse({'error': "You don't have permission to manage icons."},
                            status=403)
    icon = get_object_or_404(Icon, pk=pk)
    if not shipped_shape(icon.name)[0]:
        return JsonResponse({'error': 'This icon has no shipped shape to go back to.'},
                            status=400)
    icon.reset()
    from .models import render_icon
    return JsonResponse({'ok': True, 'message': f'“{icon}” restored.',
                         'svg': render_icon(icon.name, size=26), 'customised': False})


@require_POST
def icon_delete(request, pk):
    if not user_can_manage_icons(request.user):
        return JsonResponse({'error': "You don't have permission to manage icons."},
                            status=403)
    icon = get_object_or_404(Icon, pk=pk)
    if icon.is_builtin:
        return JsonResponse(
            {'error': 'Built-in icons cannot be deleted, because templates refer to '
                      'them by name. Use Reset to restore the original shape.'},
            status=400)
    name = str(icon)
    icon.delete()
    return JsonResponse({'ok': True, 'message': f'“{name}” deleted.', 'removed': pk})


#: How many cells to draw at once. The whole set is 1,512; a browser given all of
#: them inlines around 800 KB of path data into one grid and janks while scrolling.
PAGE = 180


def _picker_names(q, category):
    """(names, total_matches, custom_rows) for one picker request.

    Uploaded and edited icons are their own category rather than being mixed in:
    they are the ones somebody here made, and burying twenty of those among 1,512
    stock icons makes them unfindable.
    """
    # ~Q(image='') alone matches a NULL image too -- SQL says NOT (NULL = '') is
    # unknown, and Django's negation lets it through -- which made every row in
    # the library look like an upload.
    has_picture = Q(image__isnull=False) & ~Q(image='')
    custom = Icon.objects.filter(~Q(body='') | has_picture).order_by('name')

    if category == 'custom':
        rows = [i for i in custom if not q or q.lower() in i.name.lower()]
        return [i.name for i in rows], len(rows), rows

    names = phosphor.search(q, category, limit=0)

    # A library row whose name is not a Phosphor one -- an upload, or one of the
    # hand-drawn shapes -- still has to be findable by searching for it.
    if q:
        extra = [i.name for i in custom
                 if q.lower() in i.name.lower() and i.name not in names]
        names = extra + names
    return names, len(names), []


def icon_picker(request):
    """The picker body, loaded into any form that sets an icon.

    Searching happens here rather than in the browser. It used to be a client-side
    substring filter over every row in the library, which worked when there were
    76 icons and would mean shipping the entire set on every open now there are
    1,512.
    """
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Sign in first.'}, status=403)
    from django.template.loader import render_to_string

    q = (request.GET.get('q') or '').strip()
    category = (request.GET.get('cat') or '').strip()
    if not q and not category:
        category = 'common'

    names, total, custom_rows = _picker_names(q, category)
    shown = names[:PAGE]

    groups = [('common', 'Common on the Hub', 0),
              ('custom', 'Uploaded and edited', Icon.objects.exclude(body='').count())]
    groups += [g for g in phosphor.categories() if g[0] != 'common']

    ctx = {
        'names': shown,
        'total': total,
        'more': max(0, total - len(shown)),
        'q': q,
        'category': category,
        'groups': groups,
        'field': request.GET.get('field', 'icon'),
        'current': request.GET.get('current', ''),
        'can_manage': user_can_manage_icons(request.user),
        'set': phosphor.version(),
    }
    # A search or a category change replaces the grid only, so the rail keeps its
    # scroll position and the search box keeps focus and the caret.
    template = ('cms/partials/icon_picker_grid.html' if request.GET.get('grid')
                else 'cms/partials/icon_picker.html')
    return JsonResponse({
        'ok': True,
        'total': total,
        'html': render_to_string(template, ctx, request=request),
    })


def icons_js(request):
    """The icon set as a cacheable script, rather than 58 KB inside every page.

    This was the single largest thing the Hub sent: identical on every request,
    impossible to cache because it was inline, and re-parsed on every navigation.

    The URL carries a content hash, so it can be cached for a year and still
    change the instant somebody edits an icon — the stamp changes, so the URL
    changes, so the browser fetches the new one. No login is required: it is a
    set of drawings, and gating it would mean it could not be cached alongside
    the stylesheet.
    """
    from django.http import HttpResponse

    from .icon_models import icon_payload

    payload, stamp = icon_payload()
    body = f'window.AJI_ICONS = {payload};\n'
    resp = HttpResponse(body, content_type='application/javascript; charset=utf-8')
    if request.GET.get('v') == stamp:
        # Only promise immutability when the caller asked for this exact version.
        resp['Cache-Control'] = 'public, max-age=31536000, immutable'
    else:
        resp['Cache-Control'] = 'public, max-age=300'
    resp['ETag'] = f'"{stamp}"'
    return resp
