# cms/media_views.py
"""The media library behind every image field.

Before this, each image field was its own isolated upload: the same photo used on
three pages was uploaded three times, described three times, and changing it meant
finding all three. Images are now uploaded once and picked.

The store is the existing asset app rather than a second one built alongside it —
that already has thumbnails, tags, sizes and permissions, all tested. Nothing about
the Library navigation comes back; this is a picker, reachable only from a form.
"""
from functools import wraps

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from dam import thumbnails
from dam.models import Asset, Folder, Space
from .models import user_can_edit_cms

#: Where images uploaded from a content form are kept.
MEDIA_SPACE = 'page-media'
MEDIA_FOLDER = 'Uploads'

IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.svg', '.bmp')
MAX_UPLOAD = 12 * 1024 * 1024


def media_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not user_can_edit_cms(request.user):
            return JsonResponse(
                {'error': "You don't have permission to manage media."}, status=403)
        return view(request, *args, **kwargs)
    return wrapper


def uploads_folder():
    """The folder content uploads land in, created on first use.

    Made on demand rather than by a migration so an installation that never
    uploads an image never grows an empty space for one.
    """
    space, _ = Space.objects.get_or_create(
        slug=MEDIA_SPACE,
        defaults={'name': 'Page media', 'icon': 'image', 'order': 90,
                  'description': 'Images used in page content.'})
    folder, _ = Folder.objects.get_or_create(
        space=space, parent=None, name=MEDIA_FOLDER,
        defaults={'description': 'Uploaded from the page editor.'})
    return folder


def _payload(asset):
    try:
        url = asset.file.url
    except Exception:
        url = ''
    thumb = ''
    if asset.thumbnail:
        try:
            thumb = asset.thumbnail.url
        except Exception:
            thumb = ''
    from .imaging import DEFAULT_QUALITY, can_edit, lossy_format
    from dam.models import AssetVersion
    # The listing annotates this, so use the annotation when it is there. Asking
    # per asset was one extra query per image in the grid — the same N+1 the
    # template had, moved one layer down.
    has_earlier = getattr(asset, 'has_earlier', None)
    if has_earlier is None:
        has_earlier = AssetVersion.objects.filter(asset=asset).exists()
    return {
        'pk': asset.pk,
        'name': asset.name,
        'url': url,
        'thumb': thumb or url,
        'alt': asset.alt_text,
        'size': asset.pretty_size,
        'focal': [asset.focal_x, asset.focal_y],
        'kind': asset.kind,
        # The crop dialog needs the real pixel size: a rectangle measured against
        # the scaled-to-fit preview would land elsewhere on the actual file.
        'width': asset.width,
        'height': asset.height,
        'editable': can_edit(asset),
        'has_original': bool(has_earlier),
        # Whether a quality slider would do anything for this file. PNG is
        # lossless, so the control is hidden rather than left there doing nothing.
        'lossy': lossy_format(asset),
        'format': asset.format_label,
        'quality': DEFAULT_QUALITY,
    }


@media_required
def media_list(request):
    """Images available to reuse, newest first, with an optional search."""
    q = (request.GET.get('q') or '').strip()
    qs = Asset.objects.filter(archived=False)
    # Only images: this picker fills image fields, and offering a spreadsheet
    # here would only produce a broken picture on a page.
    cond = None
    from django.db.models import Q
    for ext in IMAGE_EXTS:
        cond = Q(file__iendswith=ext) if cond is None else cond | Q(file__iendswith=ext)
    qs = qs.filter(cond)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(alt_text__icontains=q))
    # Annotated rather than asked per cell: the grid shows up to 120 images, and
    # `a.versions.exists` in the template was 120 extra queries per open.
    from django.db.models import Exists, OuterRef
    from dam.models import AssetVersion
    qs = qs.annotate(has_earlier=Exists(
        AssetVersion.objects.filter(asset=OuterRef('pk'))))
    assets = list(qs.order_by('-created_at')[:120])

    # Whether quality applies is decided in one place, imaging.lossy_format, and
    # attached here. Working it out from the extension in the template as well
    # would be a second copy of the same list, free to drift.
    from .imaging import lossy_format
    for a in assets:
        a.is_lossy = lossy_format(a)

    return JsonResponse({
        'ok': True,
        'assets': [_payload(a) for a in assets],
        'html': render_to_string('cms/partials/media_grid.html',
                                {'assets': assets}, request=request),
    })


@require_POST
@media_required
def media_upload(request):
    """Add one or more images to the library and return them."""
    files = request.FILES.getlist('files') or request.FILES.getlist('file')
    if not files:
        return JsonResponse({'error': 'Choose an image to upload.'}, status=400)

    folder = uploads_folder()
    made, refused = [], []
    for upload in files:
        name_l = (upload.name or '').lower()
        if not name_l.endswith(IMAGE_EXTS):
            refused.append(f'{upload.name} is not an image')
            continue
        if upload.size > MAX_UPLOAD:
            refused.append(f'{upload.name} is over 12 MB')
            continue
        asset = Asset(folder=folder, file=upload, uploaded_by=request.user,
                      updated_by=request.user,
                      alt_text=(request.POST.get('alt') or '').strip()[:300])
        asset.save()
        if thumbnails.generate(asset):
            asset.save(update_fields=['thumbnail'])
        made.append(asset)

    if not made:
        return JsonResponse({'error': '; '.join(refused) or 'Nothing uploaded.'},
                            status=400)

    message = f'{len(made)} image(s) added.'
    if refused:
        message += ' Skipped: ' + '; '.join(refused)
    return JsonResponse({'ok': True, 'message': message,
                         'assets': [_payload(a) for a in made]})


@require_POST
@media_required
def media_update(request, pk):
    """Alt text and focal point — properties of the picture, not of one use."""
    asset = get_object_or_404(Asset, pk=pk)
    if 'alt' in request.POST:
        asset.alt_text = (request.POST.get('alt') or '').strip()[:300]
    for field, key in (('focal_x', 'focal_x'), ('focal_y', 'focal_y')):
        if key in request.POST:
            try:
                setattr(asset, field, max(0, min(100, int(request.POST[key]))))
            except (TypeError, ValueError):
                pass
    asset.updated_by = request.user
    asset.save(update_fields=['alt_text', 'focal_x', 'focal_y', 'updated_by',
                              'updated_at'])
    return JsonResponse({'ok': True, 'message': 'Saved.', 'asset': _payload(asset)})


@require_POST
@media_required
def media_transform(request, pk):
    """Crop and resize an image, keeping the previous file as a version.

    The crop arrives as percentages of the image, because the dialog shows the
    picture scaled to fit and a rectangle in screen pixels would not survive the
    trip. Everything is validated in imaging.py, which raises messages meant to be
    read by whoever is editing.
    """
    from .imaging import ImagingError, lossy_format, transform

    asset = get_object_or_404(Asset, pk=pk)

    bad = []

    def num(key, default=0):
        """A missing value falls back; a present but unreadable one is an error.

        Quietly substituting a default for 'zz' meant a garbled crop width became
        a full-width crop and the editor was told it had worked.
        """
        raw = request.POST.get(key)
        if raw in (None, ''):
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            bad.append(key)
            return default

    box = None
    if any(k in request.POST for k in ('x', 'y', 'w', 'h')):
        box = {'x': num('x', 0), 'y': num('y', 0),
               'w': num('w', 100), 'h': num('h', 100)}
        # A full-frame box is not a crop; sending it as one would burn a version
        # and re-encode the file for nothing.
        if (box['x'], box['y'], box['w'], box['h']) == (0, 0, 100, 100):
            box = None

    target_w = int(num('width', 0))
    target_h = int(num('height', 0))
    quality = int(num('quality', 0))
    if bad:
        return JsonResponse(
            {'error': 'Not a number: ' + ', '.join(sorted(set(bad))) + '.'}, status=400)
    if quality and not 40 <= quality <= 100:
        return JsonResponse(
            {'error': 'Quality must be between 40 and 100.'}, status=400)

    # Re-encoding at a new quality is a change in its own right: an image can be
    # made smaller without being cropped or resized. Quality only means anything
    # for a lossy format, so asking for it on a PNG is not a change.
    quality_only = bool(quality) and lossy_format(asset)
    if box is None and not target_w and not target_h and not quality_only:
        if quality and not lossy_format(asset):
            return JsonResponse(
                {'error': f'{asset.format_label} is lossless, so quality does not '
                          'apply. Set a crop or a new size instead.'}, status=400)
        return JsonResponse(
            {'error': 'Nothing to change — set a crop, a new size or a new quality.'},
            status=400)

    try:
        transform(asset, box=box, target_w=target_w, target_h=target_h,
                  quality=quality or None, user=request.user)
    except ImagingError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    asset.refresh_from_db()
    return JsonResponse({
        'ok': True,
        'message': f'Image updated — now {asset.width} × {asset.height}.',
        'asset': _payload(asset),
    })


@require_POST
@media_required
def media_restore(request, pk):
    """Undo the last crop or resize by putting the previous file back."""
    from .imaging import ImagingError, restore

    asset = get_object_or_404(Asset, pk=pk)
    try:
        restore(asset, user=request.user)
    except ImagingError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    asset.refresh_from_db()
    return JsonResponse({
        'ok': True,
        'message': f'Original restored — {asset.width} × {asset.height}.',
        'asset': _payload(asset),
    })
