# services/fleet_photo.py
"""Make every car picture the same shape, whatever it arrived as.

The fleet's pictures come from three places — drawings generated here, renders
made elsewhere, and screenshots cropped out of something bigger — and they
arrived 2240x920, 1536x1024 and 695x841. Five cars in a row at three aspect
ratios do not read as a set however good each one is on its own: the eye sees
the frames before it sees the cars.

So anything saved against a vehicle goes through here first. The white margin
is trimmed off, the result is centred on one canvas at one ratio, and the
background is filled to match whatever the picture's own corners are, so an
illustration on white and a render on a tinted ground both come out clean
rather than one of them sitting in a box of the wrong colour.

The original is not kept. These are pictures of cars for a list, not evidence;
re-uploading is the undo.
"""
import io

from django.core.files.base import ContentFile
from PIL import Image

#: 3:2, which is what most renders come out as and what the cards are laid out
#: for. Big enough to stay sharp on a good screen, small enough that five of
#: them are not a megabyte each.
TARGET_W, TARGET_H = 1320, 880

#: How far from the corner colour a pixel has to be before it counts as the
#: picture rather than its margin. Generous, because a white background from a
#: renderer is never quite #FFFFFF.
TOLERANCE = 12

#: Never trim away more than this. A photograph that genuinely fills its frame
#: has no margin, and an over-eager trim on one would crop into the car.
MAX_TRIM = 0.45


def _ground(im):
    """The colour of the margin, read from the four corners.

    Read rather than assumed: these pictures are as often on a tint as on
    white, and filling a tinted one with white puts a hard white band down
    the side of the car.
    """
    w, h = im.size
    corners = [im.getpixel(p) for p in
               ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    # The most common corner, so one dark corner does not decide it.
    counts = {}
    for c in corners:
        counts[c] = counts.get(c, 0) + 1
    return max(counts, key=counts.get)


def _trim(im, ground):
    """Crop away the uniform margin, within reason."""
    bg = Image.new('RGB', im.size, ground)
    from PIL import ImageChops
    diff = ImageChops.difference(im, bg).convert('L')
    box = diff.point(lambda v: 255 if v > TOLERANCE else 0).getbbox()
    if not box:
        return im
    w, h = im.size
    # Refuse a crop that would take nearly half the picture — that is a sign
    # the tolerance misread the image, not that the margin was enormous.
    if (box[2] - box[0]) < w * (1 - MAX_TRIM) and (box[3] - box[1]) < h * (1 - MAX_TRIM):
        return im
    return im.crop(box)


def normalise(source, pad=0.06):
    """One car picture, trimmed and centred on the standard canvas.

    `source` is anything PIL can open: an uploaded file, a path, a buffer.
    Returns a ContentFile ready to hand to an ImageField.
    """
    im = Image.open(source)
    if im.mode in ('RGBA', 'LA', 'P'):
        im = im.convert('RGBA')
        flat = Image.new('RGB', im.size, (255, 255, 255))
        flat.paste(im, mask=im.split()[-1])
        im = flat
    else:
        im = im.convert('RGB')

    ground = _ground(im)
    im = _trim(im, ground)

    # Fit inside the canvas, leaving a margin, then centre it.
    room_w = int(TARGET_W * (1 - pad * 2))
    room_h = int(TARGET_H * (1 - pad * 2))
    scale = min(room_w / im.width, room_h / im.height)
    if scale < 1 or scale > 1:
        im = im.resize((max(1, round(im.width * scale)),
                        max(1, round(im.height * scale))), Image.LANCZOS)

    canvas = Image.new('RGB', (TARGET_W, TARGET_H), ground)
    canvas.paste(im, ((TARGET_W - im.width) // 2, (TARGET_H - im.height) // 2))

    buf = io.BytesIO()
    canvas.save(buf, 'PNG', optimize=True)
    return ContentFile(buf.getvalue())


def save_for(vehicle, source, filename=None):
    """Normalise and attach, in one call."""
    data = normalise(source)
    vehicle.photo.save(filename or f'car-{vehicle.pk}.png', data, save=True)
    return vehicle.photo.name
