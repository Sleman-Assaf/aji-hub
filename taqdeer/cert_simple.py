"""Four simple Athar Award certificates. English only, no stamp.

These replace an earlier, busier set. That one carried a struck seal, a motif in
every corner and every label in two languages, and the result was a sheet with
a great deal on it. This set is the opposite bet: one graphic move each, three
sizes of type, and nothing that has to be explained.

Dropping the Arabic is what makes the simplicity possible rather than just
tidier. It removes five lines and about 35mm of vertical space on a sheet only
210mm tall, so the recipient's name can be the largest thing on the page by a
wide margin and everything else can have air around it.

English only, and now that is true of the data as well: the Arabic citation and
the Arabic signatory title have been dropped from the model, so there is nothing
left to pass. The bilingual layout machinery went with them. Bringing Arabic
back would be a new field and a new line here, not a flag.

Brand kit only: #FCD700, #000000, #FFFFFF, and Draft B throughout.
"""
import io
import math
import os

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas

from .certificate import (BLACK, BODY, BOLD, LOGO, LOGO_RATIO, MOTTO_LINES,
                         ORG, WHITE,
                          YELLOW, _fit, _fonts, _wrap)

CW, CH = landscape(A4)

_LOGO_DIR = os.path.dirname(LOGO)
LOGO_BLACK = LOGO
LOGO_WHITE = os.path.join(_LOGO_DIR, 'Logo_White.png')

EYEBROW = 'CERTIFICATE OF RECOGNITION'
LEDE = 'is presented to'
#: Shorter than the old wording, because at this size the sentence is read
#: rather than skimmed and "outstanding performance and achievement in" is four
#: words of throat-clearing before the part that means something.
RECOGNITION = 'In recognition of'


def _logo(c, path, x, top, w):
    """The wordmark at `w` wide with its TOP edge at `top`.

    Positioned from the top because that is the edge that collides with things.
    The file has no internal padding, so the box is the mark.
    """
    if os.path.exists(path):
        c.drawImage(path, x, top - w * LOGO_RATIO, width=w, height=w * LOGO_RATIO,
                    mask='auto', preserveAspectRatio=True, anchor='sw')


#: Proportions of the rosette in static/img/athar/, expressed as fractions of
#: the outer radius so the same medal can be drawn at any size. Drawn as vector
#: rather than placed as the PNG: at 34mm across, a 280px raster is about
#: 210dpi, which is visibly soft on a certificate somebody frames.
_M_CUSP, _M_BUMP, _M_SCALLOPS = 0.889, 0.111, 24
_M_DISC, _M_RING, _M_RING_W, _M_STAR = 0.650, 0.547, 0.034, 0.325


def _medal(c, cx, cy, r):
    """The Athar rosette: a scalloped yellow disc with a recessed black centre.

    No gradient, so the depth comes from the scalloped edge and the dark centre.
    Flat also survives being photocopied, which a certificate has to.
    """
    c.saveState()
    path = c.beginPath()
    for i in range(720):
        t = 2 * math.pi * i / 720
        rr = r * (_M_CUSP + _M_BUMP * abs(math.cos(_M_SCALLOPS * t / 2)))
        x, y = cx + rr * math.cos(t), cy + rr * math.sin(t)
        path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
    path.close()
    c.setFillColor(YELLOW)
    c.drawPath(path, stroke=0, fill=1)

    c.setFillColor(BLACK)
    c.circle(cx, cy, r * _M_DISC, stroke=0, fill=1)
    c.setStrokeColor(YELLOW)
    c.setLineWidth(r * _M_RING_W)
    c.circle(cx, cy, r * _M_RING, stroke=1, fill=0)

    star = c.beginPath()
    for i in range(16):
        t = math.radians(i * 22.5 - 90)
        rr = r * _M_STAR * (1 if i % 2 == 0 else 0.40)
        x, y = cx + rr * math.cos(t), cy + rr * math.sin(t)
        star.moveTo(x, y) if i == 0 else star.lineTo(x, y)
    star.close()
    c.setFillColor(YELLOW)
    c.drawPath(star, stroke=0, fill=1)
    c.restoreState()


def _tracked(c, x, y, text, size=8, track=1.6, centred=False, mid=None):
    """Small caps with letter-spacing, drawn a character at a time.

    ReportLab has no tracking, and an 8pt label set solid reads as a smudge.
    """
    width = sum(c.stringWidth(ch, BOLD, size) + track for ch in text) - track
    cx = (mid - width / 2) if centred else x
    c.setFont(BOLD, size)
    for ch in text:
        c.drawString(cx, y, ch)
        cx += c.stringWidth(ch, BOLD, size) + track


def _cite(c, text, y, measure, draw, size=14, step=7.6 * mm, lines=3):
    """The manager's sentence. English, at most three lines, no floor games.

    With the Arabic gone there is room for three full lines everywhere, so the
    guard that used to be needed against the citation running into the
    signature is no longer load-bearing — but the cap stays, because a manager
    who writes six lines should see that only three were printed.
    """
    for line in _wrap(c, (text or '').strip(), BOLD, size, measure)[:lines]:
        c.setFont(BOLD, size)
        draw(line, y)
        y -= step
    return y


#: How tall a scanned signature may be drawn, and how far it runs below the
#: rule. A real signature sits across the line rather than balancing on top of
#: it, so it crosses by 2mm. The height ceiling is the citation above: at three
#: lines its last descender reaches about 54mm.
SIG_MAX_H = 14 * mm
SIG_CROSS = 2 * mm


def _sig(c, x, y, width, signatory, signatory_name, signature_image,
         centred=False):
    """One rule, one name, one title."""
    put = (lambda t, yy, f, s: (c.setFont(f, s),
                                c.drawCentredString(x + width / 2, yy, t))) if centred \
        else (lambda t, yy, f, s: (c.setFont(f, s), c.drawString(x, yy, t)))
    if signature_image and os.path.exists(signature_image):
        try:
            # Scaled from the image's own aspect and centred on the rule, rather
            # than dropped into a fixed 40x12mm box. The CEO's signature is a
            # compact monogram, taller than it is wide, and a fixed wide box
            # fitted it to 11mm by its height and then anchored it to the
            # left-hand end — small, and visibly off the centre of a 60mm rule.
            from PIL import Image as _Im
            with _Im.open(signature_image) as _sig_im:
                iw, ih = _sig_im.size
            h = min(SIG_MAX_H, width * ih / iw)
            w = h * iw / ih
            c.drawImage(signature_image, x + (width - w) / 2, y - SIG_CROSS,
                        width=w, height=h, mask='auto')
        except Exception:                                       # noqa: BLE001
            pass

    c.setStrokeColor(BLACK)
    c.setLineWidth(0.7)
    c.line(x, y, x + width, y)

    # The name goes below the rule, above the position, and prints whether or
    # not a signature was uploaded. It used to be the `elif` to the image, so
    # uploading a scan silently removed the name of the person signing — which
    # is the one thing on a certificate you cannot leave to be inferred.
    c.setFillColor(BLACK)
    title_y = y - 6 * mm
    if signatory_name:
        put(signatory_name, y - 7 * mm, BODY, 10.5)
        title_y = y - 13 * mm
    if centred:
        _tracked(c, 0, title_y, signatory.upper(), 7.6, 1.3,
                 centred=True, mid=x + width / 2)
    else:
        _tracked(c, x, title_y, signatory.upper(), 7.6, 1.3)


#: The gap between the two motto lines. Tight, so they read as one lockup
#: rather than two unrelated remarks — close to the leading the brand deck sets
#: it at, which is nearer to solid than to a comfortable paragraph.
_MOTTO_LEAD = 3.5 * mm


def _foot(c, left, right, y=17 * mm, split=True, org_at=None):
    """The footer: the motto on one side, the company on the other.

    The motto leads and the company closes. It was the other way round, which
    put the small print first and the line the brand actually signs off with
    trailing away at the far edge.
    """
    c.setFillColor(BLACK)

    # The pair is centred on the company's baseline rather than stacked upward
    # from it. Stacking upward put the first line straight through the footer
    # rule on the announcement, which sits just above this band; centring keeps
    # the block clear of whatever is above and below it on both documents.
    def stack(draw, x, base):
        # Regular, not bold. The motto is set in the book weight in the brand
        # deck; bolding it made a footnote shout, and beside the company name at
        # the same size it read as the more important of the two.
        c.setFont(BODY, 8.6)
        for i, line in enumerate(MOTTO_LINES):
            draw(x, base + (0.5 - i) * _MOTTO_LEAD, line)

    if split:
        stack(c.drawString, left, y)
        c.setFont(BODY, 7.4)
        # org_at centres the company name on a given x instead of running it to
        # the right margin. The seal sits above it on the rule layout, and a
        # name flush to the margin while the seal is inset by its own radius
        # left the two a few millimetres out of line — close enough to read as
        # a mistake rather than as a pair.
        if org_at is None:
            c.drawRightString(right, y, ORG)
        else:
            c.drawCentredString(org_at, y, ORG)
    else:
        # Stacked at the left on this variant, the motto above the company.
        # Further up than the split version needs: two lines land closer to what
        # is beneath them than one line did.
        stack(c.drawString, left, y + 8.5 * mm)
        c.setFont(BODY, 7.4)
        c.drawString(left, y, ORG)


def _start(recipient_name, cycle_label, variant):
    _fonts()
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=(CW, CH))
    c.setTitle(f'The Athar Award — {recipient_name} — {cycle_label}')
    c.setAuthor('AJi Group')
    c.setSubject(f'Athar Award certificate, {variant}')
    c.setFillColor(WHITE)
    c.rect(0, 0, CW, CH, stroke=0, fill=1)
    return buf, c


def _fitted_name(c, name, size, max_w):
    return _fit(c, name, BOLD, size, max_w)


# ── 1 · RULE ─────────────────────────────────────────────────────────────

def render_rule(recipient_name, citation, cycle_label,
                signatory='Chief Executive Officer', signatory_name='',
                title='THE ATHAR AWARD', recognition_en=None,
                signature_image=None):
    """Centred, and almost nothing on it.

    One graphic move: a yellow bar across the head of the sheet, full bleed. The
    rest is type. This is the one to pick if the certificate should look like it
    came from a company that trusts its typography.
    """
    buf, c = _start(recipient_name, cycle_label, 'Rule')
    mid, left, right = CW / 2, 28 * mm, CW - 28 * mm

    c.setFillColor(YELLOW)
    c.rect(0, CH - 7 * mm, CW, 7 * mm, stroke=0, fill=1)

    _logo(c, LOGO_BLACK, mid - 12 * mm, 186 * mm, 24 * mm)

    c.setFillColor(BLACK)
    _tracked(c, 0, 158 * mm, EYEBROW, 8, 1.8, centred=True, mid=mid)

    c.setFont(BOLD, 31)
    c.drawCentredString(mid, 138 * mm, title.upper())

    c.setFont(BODY, 9.6)
    c.drawCentredString(mid, 121 * mm, LEDE)

    size = _fitted_name(c, recipient_name, 36, right - left)
    c.setFont(BOLD, size)
    c.drawCentredString(mid, 101 * mm, recipient_name)

    c.setStrokeColor(YELLOW)
    c.setLineWidth(3.2)
    c.line(mid - 22 * mm, 93 * mm, mid + 22 * mm, 93 * mm)

    c.setFillColor(BLACK)
    c.setFont(BODY, 9.4)
    c.drawCentredString(mid, 82 * mm, recognition_en or RECOGNITION)
    _cite(c, citation, 72 * mm, 168 * mm,
          lambda t, y: c.drawCentredString(mid, y, t))

    _sig(c, mid - 30 * mm, 40 * mm, 60 * mm, signatory, signatory_name,
         signature_image, centred=True)

    # Bottom-right, which is where a certificate has always carried its seal.
    # Its right edge lines up with the motto beneath it and the text measure
    # stops at 232mm, so nothing can reach it.
    #: The seal, and the company name centred under it — see _foot(org_at=).
    medal_x = right - 17 * mm
    _medal(c, medal_x, 50 * mm, 17 * mm)

    c.setFillColor(BLACK)
    _tracked(c, left, 44 * mm, 'AWARDED', 7.6, 1.3)
    c.setFont(BODY, 10)
    c.drawString(left, 38 * mm, cycle_label)

    _foot(c, left, right, org_at=medal_x)
    c.showPage()
    c.save()
    return buf.getvalue()


# ── 2 · SIDEBAR ──────────────────────────────────────────────────────────

def render_sidebar(recipient_name, citation, cycle_label,
                   signatory='Chief Executive Officer', signatory_name='',
                   title='THE ATHAR AWARD', recognition_en=None,
                   signature_image=None):
    """A yellow band down the left edge, and everything else left-aligned.

    The band is the whole graphic. It gives the sheet an obvious brand edge and
    a left margin at the same time, and it survives being photocopied.
    """
    buf, c = _start(recipient_name, cycle_label, 'Sidebar')
    band = 20 * mm
    left, right = band + 24 * mm, CW - 26 * mm

    c.setFillColor(YELLOW)
    c.rect(0, 0, band, CH, stroke=0, fill=1)

    _logo(c, LOGO_BLACK, right - 24 * mm, 188 * mm, 24 * mm)

    c.setFillColor(BLACK)
    _tracked(c, left, 166 * mm, EYEBROW, 8, 1.8)

    # Under the wordmark and sharing its right edge, opposite the award name.
    # The title sets to about 180mm at 34pt, so the medal starts at 217mm.
    _medal(c, right - 17 * mm, 140 * mm, 17 * mm)

    c.setFont(BOLD, 34)
    c.drawString(left, 143 * mm, title.upper())

    c.setFont(BODY, 9.6)
    c.drawString(left, 125 * mm, LEDE)

    size = _fitted_name(c, recipient_name, 38, right - left)
    c.setFont(BOLD, size)
    c.drawString(left, 104 * mm, recipient_name)

    c.setStrokeColor(BLACK)
    c.setLineWidth(0.7)
    c.line(left, 96 * mm, right, 96 * mm)

    c.setFillColor(BLACK)
    c.setFont(BODY, 9.4)
    c.drawString(left, 85 * mm, recognition_en or RECOGNITION)
    _cite(c, citation, 75 * mm, right - left,
          lambda t, y: c.drawString(left, y, t))

    _tracked(c, left, 44 * mm, 'AWARDED', 7.6, 1.3)
    c.setFont(BODY, 10)
    c.drawString(left, 38 * mm, cycle_label)

    _sig(c, right - 60 * mm, 42 * mm, 60 * mm, signatory, signatory_name,
         signature_image)

    _foot(c, left, right)
    c.showPage()
    c.save()
    return buf.getvalue()


# ── 3 · WEDGE ────────────────────────────────────────────────────────────

def render_wedge(recipient_name, citation, cycle_label,
                 signatory='Chief Executive Officer', signatory_name='',
                 title='THE ATHAR AWARD', recognition_en=None,
                 signature_image=None):
    """One yellow diagonal out of the bottom-right corner.

    The most graphic of the four while still being one shape. The text column
    stops at 168mm and the wedge starts at 176mm, so they cannot meet however
    long the citation runs.
    """
    buf, c = _start(recipient_name, cycle_label, 'Wedge')
    left, right = 30 * mm, CW - 26 * mm

    wedge = c.beginPath()
    wedge.moveTo(176 * mm, 0)
    wedge.lineTo(CW, 0)
    wedge.lineTo(CW, 112 * mm)
    wedge.close()
    c.setFillColor(YELLOW)
    c.drawPath(wedge, stroke=0, fill=1)

    _logo(c, LOGO_BLACK, left, 190 * mm, 25 * mm)

    c.setFillColor(BLACK)
    _tracked(c, left, 166 * mm, EYEBROW, 8, 1.8)

    c.setFont(BOLD, 35)
    c.drawString(left, 142 * mm, title.upper())
    c.setStrokeColor(YELLOW)
    c.setLineWidth(3.4)
    c.line(left, 133 * mm, left + 30 * mm, 133 * mm)

    c.setFillColor(BLACK)
    c.setFont(BODY, 9.6)
    c.drawString(left, 121 * mm, LEDE)

    measure = 138 * mm
    size = _fitted_name(c, recipient_name, 36, measure)
    c.setFont(BOLD, size)
    c.drawString(left, 100 * mm, recipient_name)

    c.setStrokeColor(BLACK)
    c.setLineWidth(0.7)
    c.line(left, 92 * mm, left + measure, 92 * mm)

    c.setFillColor(BLACK)
    c.setFont(BODY, 9.4)
    c.drawString(left, 81 * mm, recognition_en or RECOGNITION)
    _cite(c, citation, 71 * mm, measure, lambda t, y: c.drawString(left, y, t))

    # 38mm rather than 46: this is the one template whose measure is narrow
    # enough that the citation reliably runs to three lines, and at 46 the last
    # of them sat 6mm off the signature.
    _tracked(c, left, 38 * mm, 'AWARDED', 7.6, 1.3)
    c.setFont(BODY, 10)
    c.drawString(left, 32 * mm, cycle_label)

    _sig(c, left + 50 * mm, 38 * mm, 60 * mm, signatory, signatory_name,
         signature_image)

    # Motto on the left: the bottom-right corner is the wedge.
    _foot(c, left, right, y=15 * mm, split=False)
    c.showPage()
    c.save()
    return buf.getvalue()


# ── 4 · HEADER ───────────────────────────────────────────────────────────

def render_header(recipient_name, citation, cycle_label,
                  signatory='Chief Executive Officer', signatory_name='',
                  title='THE ATHAR AWARD', recognition_en=None,
                  signature_image=None):
    """A solid yellow head carrying the wordmark and the award name.

    The boldest of the four and still only two blocks: yellow above, white
    below. Reads from across a room, which is what a framed certificate has to
    do.
    """
    buf, c = _start(recipient_name, cycle_label, 'Header')
    head = 62 * mm
    base = CH - head
    left, right = 30 * mm, CW - 30 * mm

    c.setFillColor(YELLOW)
    c.rect(0, base, CW, head, stroke=0, fill=1)

    _logo(c, LOGO_BLACK, left, CH - 14 * mm, 24 * mm)

    c.setFillColor(BLACK)
    _tracked(c, left, base + 26 * mm, EYEBROW, 8, 1.8)
    c.setFont(BOLD, 33)
    c.drawString(left, base + 10 * mm, title.upper())
    c.setFont(BODY, 9.4)
    c.drawRightString(right, base + 10 * mm, cycle_label)

    c.setFont(BODY, 9.6)
    c.drawString(left, 130 * mm, LEDE)

    size = _fitted_name(c, recipient_name, 38, right - left)
    c.setFont(BOLD, size)
    c.drawString(left, 108 * mm, recipient_name)

    c.setStrokeColor(BLACK)
    c.setLineWidth(0.7)
    c.line(left, 100 * mm, right, 100 * mm)

    c.setFillColor(BLACK)
    c.setFont(BODY, 9.4)
    c.drawString(left, 89 * mm, recognition_en or RECOGNITION)
    _cite(c, citation, 79 * mm, right - left,
          lambda t, y: c.drawString(left, y, t))

    _sig(c, left, 44 * mm, 60 * mm, signatory, signatory_name, signature_image)
    _foot(c, left, right)
    c.showPage()
    c.save()
    return buf.getvalue()


#: The four, by the key HR would pick from.
TEMPLATES = {
    'rule': ('Rule', 'Centred. One yellow bar across the head, the rest is type.',
             render_rule),
    'sidebar': ('Sidebar', 'A yellow band down the left edge.', render_sidebar),
    'wedge': ('Wedge', 'One yellow diagonal out of the bottom-right corner.',
              render_wedge),
    'header': ('Header', 'A solid yellow head carrying the wordmark and the name.',
               render_header),
}
