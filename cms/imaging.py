# cms/imaging.py
"""Cropping and resizing an image already in the library.

Before this, getting a differently-shaped version of a photo meant cropping it
somewhere else and uploading it again, which left two near-identical images in
the library and no way to tell which pages used which.

Three rules shape what follows:

Nothing is destructive. The file being replaced is kept as an AssetVersion first,
so a crop can always be undone — the version machinery already existed for
replacing a file and is reused rather than duplicated.

The crop arrives as percentages, never pixels. The dialog shows the photo scaled
to fit, so a rectangle measured in screen pixels would land somewhere else on the
real file. Percentages are the only thing both sides agree on.

Metadata is dropped on re-encode. A phone photo carries GPS coordinates, and an
intranet page is the wrong place to publish where an employee's house is. The one
piece of metadata that is honoured first is EXIF orientation, because the crop has
to match the picture as the editor saw it, not as the sensor recorded it.
"""
import io
import os

from django.core.files.base import ContentFile

#: Nothing useful comes of a crop smaller than this, and a stray click that
#: produced a 2px image would look like data loss.
MIN_SIDE = 16

#: An upper bound on output, so a resize cannot be asked to allocate gigabytes.
MAX_SIDE = 8000

#: Vector files are excluded. Rasterising an SVG to crop it would throw away the
#: only reason to use one, and cropping the markup is a different job entirely.
VECTOR_EXTS = ('.svg',)

#: Formats that survive a round trip. Anything else is written as PNG, which
#: never loses more than it has to.
KEEP_FORMATS = {'.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG',
                '.webp': 'WEBP', '.bmp': 'PNG', '.gif': 'PNG'}

DEFAULT_QUALITY = 86


class ImagingError(Exception):
    """Something about the request or the file makes the edit impossible."""


#: Formats where a quality setting means something. PNG is lossless, so offering
#: a quality slider for one would be a control that does nothing.
LOSSY_FORMATS = {'JPEG', 'WEBP'}


def can_edit(asset):
    """Whether this asset is a raster image this module can rewrite."""
    return bool(asset and asset.kind == 'image' and asset.ext not in VECTOR_EXTS)


def lossy_format(asset):
    """Whether quality applies to what this asset will be written as.

    Asked of the output format rather than the input: a transparent JPEG is
    written as PNG, at which point quality stops meaning anything.
    """
    if not can_edit(asset):
        return False
    return KEEP_FORMATS.get(asset.ext, 'PNG') in LOSSY_FORMATS


def _target_format(ext, has_alpha):
    fmt = KEEP_FORMATS.get(ext, 'PNG')
    # A JPEG cannot hold transparency; writing one would fill it with black.
    if fmt == 'JPEG' and has_alpha:
        return 'PNG', '.png'
    return fmt, ext if ext in KEEP_FORMATS and KEEP_FORMATS[ext] == fmt else (
        '.png' if fmt == 'PNG' else ext)


def _pct(value, name):
    try:
        out = float(value)
    except (TypeError, ValueError):
        raise ImagingError(f'{name} is not a number.')
    if not (0 <= out <= 100):
        raise ImagingError(f'{name} must be between 0 and 100.')
    return out


def plan_crop(width, height, box):
    """Turn a percentage box into pixel bounds on a real image.

    Returns (left, top, right, bottom). Raised errors are for the editor to read,
    so they say what is wrong rather than what failed.
    """
    if not width or not height:
        raise ImagingError('The size of this image could not be read.')

    x = _pct(box.get('x', 0), 'Crop left')
    y = _pct(box.get('y', 0), 'Crop top')
    w = _pct(box.get('w', 100), 'Crop width')
    h = _pct(box.get('h', 100), 'Crop height')

    left = int(round(width * x / 100))
    top = int(round(height * y / 100))
    right = int(round(width * (x + w) / 100))
    bottom = int(round(height * (y + h) / 100))

    # Clamp inside the image rather than rejecting a rectangle that a rounded
    # percentage pushed a pixel over the edge.
    left, top = max(0, min(left, width - 1)), max(0, min(top, height - 1))
    right, bottom = min(width, max(right, left + 1)), min(height, max(bottom, top + 1))

    if right - left < MIN_SIDE or bottom - top < MIN_SIDE:
        raise ImagingError(f'That crop is smaller than {MIN_SIDE} pixels.')
    return left, top, right, bottom


def plan_resize(crop_w, crop_h, target_w, target_h):
    """The output size, keeping the crop's shape when only one side is given."""
    tw = int(target_w or 0)
    th = int(target_h or 0)
    if tw <= 0 and th <= 0:
        return crop_w, crop_h
    if tw > 0 and th <= 0:
        th = max(1, int(round(crop_h * tw / crop_w)))
    elif th > 0 and tw <= 0:
        tw = max(1, int(round(crop_w * th / crop_h)))
    if tw < 1 or th < 1:
        raise ImagingError('The size asked for is too small.')
    if tw > MAX_SIDE or th > MAX_SIDE:
        raise ImagingError(f'The largest side supported is {MAX_SIDE} pixels.')
    return tw, th


def transform(asset, box=None, target_w=0, target_h=0, quality=DEFAULT_QUALITY,
              user=None, note=''):
    """Crop and/or resize the asset's file in place, keeping the old one.

    Returns the AssetVersion holding what was replaced, so a caller can tell the
    editor there is something to restore.
    """
    if not can_edit(asset):
        raise ImagingError('Only photographs and other raster images can be cropped.')

    from PIL import Image, ImageOps
    from dam.models import AssetVersion

    try:
        asset.file.open('rb')
        raw = asset.file.read()
    except Exception:
        raise ImagingError('The image file could not be read.')
    finally:
        try:
            asset.file.close()
        except Exception:
            pass

    try:
        with Image.open(io.BytesIO(raw)) as im:
            im = ImageOps.exif_transpose(im)
            # A palette image crops fine but resizes badly; RGB(A) resamples cleanly.
            if im.mode in ('P', 'LA', 'PA'):
                im = im.convert('RGBA' if 'A' in im.mode else 'RGB')
            has_alpha = im.mode in ('RGBA', 'LA') or (
                im.mode == 'P' and 'transparency' in im.info)

            width, height = im.size
            if box:
                left, top, right, bottom = plan_crop(width, height, box)
                im = im.crop((left, top, right, bottom))
            crop_w, crop_h = im.size

            out_w, out_h = plan_resize(crop_w, crop_h, target_w, target_h)
            if (out_w, out_h) != (crop_w, crop_h):
                im = im.resize((out_w, out_h), Image.LANCZOS)

            fmt, new_ext = _target_format(asset.ext, has_alpha)
            if fmt == 'JPEG' and im.mode != 'RGB':
                im = im.convert('RGB')

            buf = io.BytesIO()
            params = {}
            if fmt in ('JPEG', 'WEBP'):
                params['quality'] = max(40, min(100, int(quality or DEFAULT_QUALITY)))
                params['optimize'] = True
            if fmt == 'PNG':
                params['optimize'] = True
            # No exif= and no icc_profile: re-encoding is where the GPS tags go.
            im.save(buf, format=fmt, **params)
            payload = buf.getvalue()
            final_size = (out_w, out_h)
    except ImagingError:
        raise
    except Exception as exc:
        raise ImagingError(f'That image could not be edited ({exc.__class__.__name__}).')

    # Keep what is being replaced BEFORE overwriting, so a failure part-way
    # through cannot lose the original.
    version = AssetVersion.objects.create(
        asset=asset, version=asset.version, size=asset.size,
        note=(note or 'Before cropping')[:300], replaced_by=user)
    version.file.save(asset.filename, ContentFile(raw), save=True)

    stem = os.path.splitext(asset.filename)[0][:80] or 'image'
    asset.file.save(f'{stem}{new_ext}', ContentFile(payload), save=False)
    asset.version = (asset.version or 1) + 1
    asset.width, asset.height = final_size
    # The subject is somewhere else now, so a focal point measured against the
    # old frame would point at the wrong thing. Centre is the honest default.
    asset.focal_x = asset.focal_y = 50
    if user is not None:
        asset.updated_by = user
    asset.save()

    from dam import thumbnails
    if thumbnails.generate(asset):
        asset.save(update_fields=['thumbnail'])
    return version


def restore(asset, user=None):
    """Put the most recently replaced file back."""
    from dam.models import AssetVersion

    version = (AssetVersion.objects.filter(asset=asset)
               .order_by('-version', '-created_at').first())
    if version is None or not version.file:
        raise ImagingError('There is no earlier version of this image to restore.')

    try:
        version.file.open('rb')
        raw = version.file.read()
    except Exception:
        raise ImagingError('The earlier version could not be read.')
    finally:
        try:
            version.file.close()
        except Exception:
            pass

    name = version.file.name.rsplit('/', 1)[-1] or asset.filename
    asset.file.save(name, ContentFile(raw), save=False)
    asset.focal_x = asset.focal_y = 50
    if user is not None:
        asset.updated_by = user
    asset.save()

    from dam import thumbnails
    if thumbnails.generate(asset):
        asset.save(update_fields=['thumbnail'])

    # The restored file is no longer a pending change, so the version goes.
    version.file.delete(save=False)
    version.delete()
    return asset
