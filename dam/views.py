# dam/views.py
"""One file browser, reused by every module.

Every mutating operation returns JSON and the client re-fetches only the grid
fragment, so folder, search, filters, sort and scroll position all survive —
the previous modules full-reloaded and jumped to the top on every action.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from . import thumbnails
from .models import (Asset, AssetVersion, Folder, Space, Tag, audience_allows,
                     user_can_manage_dam)


def dam_manager_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not user_can_manage_dam(request.user):
            return JsonResponse({'error': "You don't have permission to change this library."},
                                status=403)
        return view(request, *args, **kwargs)
    return wrapper


# ── browsing ──────────────────────────────────────────────────────────────

def _visible_folders(parent, space, user):
    qs = Folder.objects.filter(space=space, parent=parent).select_related('space', 'parent')
    return [f for f in qs if f.is_visible_to(user)]


def visible_folder_ids(space, user):
    """Every folder in this space the user may see, ancestor rules cascaded.

    Resolved in one pass over the space's folders rather than an ancestor walk
    per row, because search and tag filters span the whole space and would
    otherwise run a query per result.
    """
    if not space.is_visible_to(user):
        return set()

    folders = list(space.folders.all())
    by_pk = {f.pk: f for f in folders}
    allowed = {f.pk: audience_allows(f.audience, f.audience_groups, user)
               for f in folders}

    visible = set()
    for f in folders:
        node, guard, ok = f, 0, True
        while node is not None and guard < 50:
            if not allowed.get(node.pk, False):
                ok = False
                break
            node = by_pk.get(node.parent_id)
            guard += 1
        if ok:
            visible.add(f.pk)
    return visible


def _asset_queryset(request, folder, space, visible_ids=None):
    """Assets for the current view, with search, tag, type and sort applied."""
    show_archived = request.GET.get('archived') == '1'
    q = (request.GET.get('q') or '').strip()
    kind = (request.GET.get('kind') or '').strip()
    tag = (request.GET.get('tag') or '').strip()

    # Any active filter searches the whole space; with no filter you see the
    # folder you are standing in. Scoping filters to one level meant a tag on a
    # nested file could never be found without already knowing where it lived.
    filtering = bool(q or kind or tag)

    if filtering:
        qs = Asset.objects.filter(folder__space=space)
    elif folder is not None:
        qs = Asset.objects.filter(folder=folder)
    else:
        qs = Asset.objects.filter(folder__space=space, folder__parent__isnull=True)

    qs = qs.filter(archived=show_archived)

    # A restricted folder hides its files from search too. Without this the
    # folder disappeared from the tree but its contents still surfaced under any
    # search, tag or type filter, which spans the whole space by design.
    if visible_ids is None:
        visible_ids = visible_folder_ids(space, request.user)
    qs = qs.filter(folder_id__in=visible_ids)

    if q:
        qs = qs.filter(
            Q(name__icontains=q) | Q(description__icontains=q)
            | Q(file__icontains=q) | Q(tags__name__icontains=q)).distinct()

    if kind:
        ext_map = {'image': Asset.IMAGE, 'pdf': Asset.PDF, 'doc': Asset.DOC,
                   'sheet': Asset.SHEET, 'slide': Asset.SLIDE,
                   'video': Asset.VIDEO, 'archive': Asset.ARCHIVE}
        exts = ext_map.get(kind)
        if exts:
            cond = Q()
            for e in exts:
                cond |= Q(file__iendswith=e)
            qs = qs.filter(cond)

    if tag:
        qs = qs.filter(tags__slug=tag)

    sort = request.GET.get('sort') or 'name'
    order = {'name': 'name', '-name': '-name', 'new': '-created_at',
             'old': 'created_at', 'big': '-size', 'small': 'size',
             'updated': '-updated_at'}.get(sort, 'name')
    return qs.select_related('folder', 'uploaded_by', 'updated_by').prefetch_related('tags').order_by(order)


def _browse_context(request, space, folder):
    visible_ids = visible_folder_ids(space, request.user)
    assets = list(_asset_queryset(request, folder, space, visible_ids))
    folders = _visible_folders(folder, space, request.user)

    # Storage is reported over what this user can see, so the figure always
    # matches the files they can actually reach.
    totals = Asset.objects.filter(folder_id__in=visible_ids, archived=False).aggregate(
        n=Count('pk'), bytes=Sum('size'))

    return {
        'space': space,
        'folder': folder,
        'breadcrumbs': folder.ancestors_and_self() if folder else [],
        'folders': folders,
        'assets': assets,
        # Same reasoning as the asset filter: a tag used only inside a restricted
        # folder must not appear in the filter bar.
        'tags': Tag.objects.filter(assets__folder_id__in=visible_ids).distinct(),
        'can_manage': user_can_manage_dam(request.user),
        'view_mode': request.GET.get('view') or 'grid',
        'q': request.GET.get('q', ''),
        'kind': request.GET.get('kind', ''),
        'tag': request.GET.get('tag', ''),
        'sort': request.GET.get('sort', 'name'),
        'archived': request.GET.get('archived') == '1',
        'storage': {'count': totals['n'] or 0, 'bytes': totals['bytes'] or 0,
                    'pretty': _pretty(totals['bytes'] or 0)},
        'thumb_caps': thumbnails.capabilities(),
        'move_targets': _move_targets(space, request.user),
    }


def _pretty(n):
    n = float(n or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return f'{n:.0f} {unit}' if unit in ('B', 'KB') else f'{n:.1f} {unit}'
        n /= 1024
    return f'{n:.1f} GB'


def _move_targets(space, user):
    """Flat folder list with indentation, for the Move dropdown."""
    out = []

    def walk(parent, depth):
        for f in Folder.objects.filter(space=space, parent=parent).order_by('name'):
            if f.is_visible_to(user):
                out.append({'pk': f.pk, 'label': ('— ' * depth) + f.name})
                walk(f, depth + 1)
    walk(None, 0)
    return out


@login_required
def space_list(request):
    spaces = []
    for space in Space.objects.prefetch_related('audience_groups'):
        if not space.is_visible_to(request.user):
            continue
        # Counted over what this user may see, so the index never advertises
        # files they cannot reach.
        visible = visible_folder_ids(space, request.user)
        totals = Asset.objects.filter(folder_id__in=visible, archived=False).aggregate(
            n=Count('pk'), bytes=Sum('size'))
        space.file_count = totals['n'] or 0
        space.folder_count = len(visible)
        space.pretty_size = _pretty(totals['bytes'] or 0)
        spaces.append(space)
    return render(request, 'dam/spaces.html', {
        'spaces': spaces, 'can_manage': user_can_manage_dam(request.user)})


@login_required
def browse(request, slug, folder_pk=None):
    space = get_object_or_404(Space, slug=slug)
    if not space.is_visible_to(request.user):
        raise Http404

    folder = None
    if folder_pk:
        folder = get_object_or_404(Folder, pk=folder_pk, space=space)
        if not folder.is_visible_to(request.user):
            raise Http404

    ctx = _browse_context(request, space, folder)
    # The client asks for just the grid after an operation, so nothing else
    # on the page is disturbed.
    if request.GET.get('fragment') == '1':
        return render(request, 'dam/_browser_body.html', ctx)
    return render(request, 'dam/browse.html', ctx)


# ── folder operations ─────────────────────────────────────────────────────

@login_required
@require_POST
@dam_manager_required
def folder_create(request, slug):
    space = get_object_or_404(Space, slug=slug)
    name = (request.POST.get('name') or '').strip()
    if not name:
        return JsonResponse({'error': 'Give the folder a name.'}, status=400)
    parent = None
    if request.POST.get('parent'):
        parent = get_object_or_404(Folder, pk=request.POST['parent'], space=space)
    f = Folder.objects.create(space=space, parent=parent, name=name[:200],
                              owner=request.user)
    return JsonResponse({'ok': True, 'message': f'Folder “{f.name}” created.', 'id': f.pk})


@login_required
@require_POST
@dam_manager_required
def folder_update(request, slug, folder_pk):
    space = get_object_or_404(Space, slug=slug)
    folder = get_object_or_404(Folder, pk=folder_pk, space=space)
    action = request.POST.get('action') or 'rename'

    if action == 'rename':
        name = (request.POST.get('name') or '').strip()
        if not name:
            return JsonResponse({'error': 'Give the folder a name.'}, status=400)
        folder.name = name[:200]
        folder.save(update_fields=['name'])
        return JsonResponse({'ok': True, 'message': 'Folder renamed.'})

    if action == 'pin':
        folder.pinned = not folder.pinned
        folder.save(update_fields=['pinned'])
        return JsonResponse({'ok': True,
                             'message': 'Pinned.' if folder.pinned else 'Unpinned.'})

    if action == 'colour':
        folder.colour = request.POST.get('colour', '')[:10]
        folder.save(update_fields=['colour'])
        return JsonResponse({'ok': True, 'message': 'Label updated.'})

    if action == 'move':
        target = None
        if request.POST.get('parent'):
            target = get_object_or_404(Folder, pk=request.POST['parent'], space=space)
        # Moving a folder inside itself would detach the whole subtree.
        if folder.would_create_cycle(target):
            return JsonResponse(
                {'error': 'A folder cannot be moved inside itself.'}, status=400)
        folder.parent = target
        folder.save(update_fields=['parent'])
        return JsonResponse({'ok': True, 'message': 'Folder moved.'})

    if action == 'delete':
        if folder.asset_count:
            return JsonResponse(
                {'error': f'“{folder.name}” still holds {folder.asset_count} '
                          'file(s). Move or archive them first.'}, status=400)
        name = folder.name
        folder.delete()
        return JsonResponse({'ok': True, 'message': f'Folder “{name}” deleted.'})

    return JsonResponse({'error': 'Unknown action.'}, status=400)


# ── asset operations ──────────────────────────────────────────────────────

@login_required
@require_POST
@dam_manager_required
def asset_upload(request, slug):
    space = get_object_or_404(Space, slug=slug)
    folder_pk = request.POST.get('folder')
    if not folder_pk:
        return JsonResponse({'error': 'Choose a folder to upload into.'}, status=400)
    folder = get_object_or_404(Folder, pk=folder_pk, space=space)

    files = request.FILES.getlist('files') or request.FILES.getlist('file')
    if not files:
        return JsonResponse({'error': 'No files received.'}, status=400)

    MAX = 50 * 1024 * 1024
    created, skipped = [], []
    for f in files:
        if f.size > MAX:
            skipped.append(f'{f.name} (over 50 MB)')
            continue
        a = Asset(folder=folder, file=f, uploaded_by=request.user,
                  updated_by=request.user)
        a.save()
        if thumbnails.generate(a):
            a.save(update_fields=['thumbnail'])
        created.append(a.name)

    msg = f'{len(created)} file(s) uploaded.' if created else 'Nothing uploaded.'
    if skipped:
        msg += ' Skipped: ' + ', '.join(skipped)
    return JsonResponse({'ok': bool(created), 'message': msg,
                         'created': created, 'skipped': skipped},
                        status=200 if created else 400)


@login_required
@require_POST
@dam_manager_required
def asset_update(request, slug, asset_pk):
    space = get_object_or_404(Space, slug=slug)
    asset = get_object_or_404(Asset, pk=asset_pk, folder__space=space)
    action = request.POST.get('action') or 'rename'

    if action == 'rename':
        name = (request.POST.get('name') or '').strip()
        if not name:
            return JsonResponse({'error': 'Give the file a name.'}, status=400)
        asset.name = name[:250]
        asset.description = request.POST.get('description', asset.description)[:500]
        asset.updated_by = request.user
        asset.save()
        _apply_tags(asset, request.POST.get('tags'))
        return JsonResponse({'ok': True, 'message': 'Details saved.'})

    if action == 'replace':
        upload = request.FILES.get('file')
        if not upload:
            return JsonResponse({'error': 'Choose a replacement file.'}, status=400)
        # Keep the outgoing file as a version so replace is never destructive.
        AssetVersion.objects.create(
            asset=asset, file=asset.file, version=asset.version,
            note=request.POST.get('note', '')[:300], size=asset.size,
            replaced_by=request.user)
        asset.file = upload
        asset.version += 1
        asset.updated_by = request.user
        asset.thumbnail = None
        asset.save()
        if thumbnails.generate(asset):
            asset.save(update_fields=['thumbnail'])
        return JsonResponse({'ok': True,
                             'message': f'Replaced — now version {asset.version}.'})

    if action == 'move':
        target = get_object_or_404(Folder, pk=request.POST.get('folder'), space=space)
        asset.folder = target
        asset.updated_by = request.user
        asset.save(update_fields=['folder', 'updated_by', 'updated_at'])
        return JsonResponse({'ok': True, 'message': f'Moved to “{target.name}”.'})

    if action == 'duplicate':
        copy = Asset.objects.get(pk=asset.pk)
        copy.pk = None
        copy.name = f'{asset.name} (copy)'[:250]
        copy.version = 1
        copy.uploaded_by = request.user
        copy.save()
        copy.tags.set(asset.tags.all())
        return JsonResponse({'ok': True, 'message': 'Duplicated.'})

    if action in ('archive', 'restore'):
        asset.archived = (action == 'archive')
        asset.updated_by = request.user
        asset.save(update_fields=['archived', 'updated_by', 'updated_at'])
        return JsonResponse({'ok': True,
                             'message': 'Archived.' if asset.archived else 'Restored.'})

    if action == 'delete':
        name = asset.name
        asset.delete()
        return JsonResponse({'ok': True, 'message': f'“{name}” deleted permanently.'})

    return JsonResponse({'error': 'Unknown action.'}, status=400)


def _apply_tags(asset, raw):
    if raw is None:
        return
    names = [t.strip() for t in raw.split(',') if t.strip()][:12]
    tags = []
    for n in names:
        tag, _ = Tag.objects.get_or_create(
            slug=slugify(n)[:50] or 'tag', defaults={'name': n[:60]})
        tags.append(tag)
    asset.tags.set(tags)


@login_required
@require_POST
@dam_manager_required
def asset_bulk(request, slug):
    """Bulk archive, restore, delete or move for a selection of assets."""
    space = get_object_or_404(Space, slug=slug)
    ids = request.POST.getlist('ids[]') or request.POST.getlist('ids')
    action = request.POST.get('action')
    assets = list(Asset.objects.filter(pk__in=ids, folder__space=space))
    if not assets:
        return JsonResponse({'error': 'Nothing selected.'}, status=400)

    if action == 'delete':
        n = len(assets)
        for a in assets:
            a.delete()
        return JsonResponse({'ok': True, 'message': f'{n} file(s) deleted.'})

    if action in ('archive', 'restore'):
        flag = action == 'archive'
        Asset.objects.filter(pk__in=[a.pk for a in assets]).update(archived=flag)
        return JsonResponse({'ok': True,
                             'message': f'{len(assets)} file(s) '
                                        + ('archived.' if flag else 'restored.')})

    if action == 'move':
        target = get_object_or_404(Folder, pk=request.POST.get('folder'), space=space)
        Asset.objects.filter(pk__in=[a.pk for a in assets]).update(folder=target)
        return JsonResponse({'ok': True,
                             'message': f'{len(assets)} file(s) moved to “{target.name}”.'})

    if action == 'tag':
        for a in assets:
            _apply_tags(a, request.POST.get('tags'))
        return JsonResponse({'ok': True, 'message': f'{len(assets)} file(s) tagged.'})

    return JsonResponse({'error': 'Unknown action.'}, status=400)


@login_required
def asset_detail(request, slug, asset_pk):
    """Metadata, preview and version history for one asset."""
    space = get_object_or_404(Space, slug=slug)
    asset = get_object_or_404(Asset, pk=asset_pk, folder__space=space)
    if not asset.is_visible_to(request.user):
        raise Http404
    return render(request, 'dam/asset_detail.html', {
        'space': space, 'asset': asset,
        'versions': asset.versions.select_related('replaced_by'),
        'breadcrumbs': asset.folder.ancestors_and_self(),
        'can_manage': user_can_manage_dam(request.user),
        'move_targets': _move_targets(space, request.user),
    })
