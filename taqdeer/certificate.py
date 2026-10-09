"""The Athar Award certificate.

Generated on approval, not designed by hand, so a hundred of them are identical
and a name correction is a re-render rather than a redraw.

Brand rules, per the kit: three colours only — #FCD700, #000000, #FFFFFF — and
two faces. Draft B is the display face and holds no Arabic glyph at all, so any
line carrying Arabic is set wholly in IBM Plex Sans Arabic rather than stitched
from two fonts mid-line. The .ttf conversions under static/fonts/ are the ones
the existing exports already use; ReportLab will not embed the shipped CFF .otf.

The logo is the corporate AJi wordmark from Brand Files — not the AJi Hub lockup
that appears in the intranet chrome. A certificate comes from the company, not
from the software that printed it.
"""
import io
import math
import os
import re

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas

YELLOW = colors.HexColor('#FCD700')
BLACK = colors.HexColor('#000000')
WHITE = colors.HexColor('#FFFFFF')

BODY, BOLD = 'DraftB-Regular', 'DraftB-Bold'
AR_BODY, AR_BOLD = 'PlexArabic-Regular', 'PlexArabic-Bold'

FONT_DIR = os.path.join(settings.BASE_DIR, 'static', 'fonts')
#: The corporate wordmark, shipped through the Brand Files module.
LOGO = os.path.join(settings.BASE_DIR, 'media', 'brand', 'templates',
                    'aji_logo', 'png', 'Logo_Black.png')
LOGO_RATIO = 4971 / 6804

#: The company, as it is called. The footers carried
#: "AJi Group  ·  Arabtech Jardaneh", the old name alongside the current one,
#: which on a certificate somebody frames reads as two companies rather than a
#: history. Named here so the certificate and the announcement cannot disagree.
ORG = 'AJi Group'

#: From the official AJi template deck. Set as two lines, because it is two
#: sentences and a lockup: "Design smart." over "Build better.", not a single
#: run of small print trailing off the right-hand edge.
MOTTO_LINES = ('Design smart.', 'Build better.')
#: The one-line form, for anywhere a single string is what fits.
MOTTO = ' '.join(MOTTO_LINES)

ARABIC_RE = re.compile(r'[؀-ۿݐ-ݿࢠ-ࣿ'
                       r'ﭐ-﷿ﹰ-﻿]')

_READY = False


def _fonts():
    global _READY
    if _READY:
        return
    for name, filename in ((BODY, 'DraftB-Regular.ttf'), (BOLD, 'DraftB-Bold.ttf'),
                           (AR_BODY, 'PlexArabic-Regular.ttf'),
                           (AR_BOLD, 'PlexArabic-Bold.ttf')):
        path = os.path.join(FONT_DIR, filename)
        if not os.path.exists(path):
            raise RuntimeError(f'AJi brand font missing: {filename}')
        pdfmetrics.registerFont(TTFont(name, path))
    _READY = True


def ar(text):
    """Reshape and bidi-order Arabic so it renders joined and right-to-left.

    ReportLab draws codepoints in the order given; raw Arabic comes out
    disconnected and backwards.
    """
    if not text:
        return ''
    import arabic_reshaper
    from bidi.algorithm import get_display
    return get_display(arabic_reshaper.reshape(text))


def _fit(c, text, font, size, max_w, floor=11):
    """Shrink until it fits. A long Arabic name is not a reason to overflow."""
    while size > floor and c.stringWidth(text, font, size) > max_w:
        size -= 0.5
    return size


def _wrap(c, text, font, size, max_w):
    words, lines, line = text.split(), [], ''
    for w in words:
        trial = f'{line} {w}'.strip()
        if c.stringWidth(trial, font, size) <= max_w:
            line = trial
        else:
            if line:
                lines.append(line)
            line = w
    if line:
        lines.append(line)
    return lines


CW, CH = landscape(A4)

RECOGNITION_EN = 'In recognition of the outstanding performance and achievement in'


def _milled(c, cx, cy, r, teeth=36):
    """The struck edge of a coin: short radial ticks inside the rim."""
    c.setStrokeColor(BLACK)
    c.setLineWidth(max(0.5, r * 0.028))
    for i in range(teeth):
        t = math.radians(i * (360 / teeth))
        c.line(cx + r * 0.905 * math.cos(t), cy + r * 0.905 * math.sin(t),
               cx + r * 0.985 * math.cos(t), cy + r * 0.985 * math.sin(t))


def _star(c, cx, cy, r, points=8):
    """A struck star. The oldest award device there is, and legible at 10mm."""
    path = c.beginPath()
    for i in range(points * 2):
        t = math.radians(i * 180 / points - 90)
        rr = r if i % 2 == 0 else r * 0.42
        x, y = cx + rr * math.cos(t), cy + rr * math.sin(t)
        path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
    path.close()
    c.drawPath(path, stroke=0, fill=1)


def _leaf(c, x, y, length, width, angle):
    """One laurel leaf: a lens, pointed at both ends, rotated into place."""
    c.saveState()
    c.translate(x, y)
    c.rotate(angle)
    pts = []
    for i in range(0, 181, 12):
        a = math.radians(i)
        pts.append((length * (i / 180) - length / 2, width / 2 * math.sin(a)))
    for i in range(180, -1, -12):
        a = math.radians(i)
        pts.append((length * (i / 180) - length / 2, -width / 2 * math.sin(a)))
    path = c.beginPath()
    path.moveTo(*pts[0])
    for pt in pts[1:]:
        path.lineTo(*pt)
    path.close()
    c.drawPath(path, stroke=0, fill=1)
    c.restoreState()


def _sprig(c, cx, cy, r, a0, a1, leaves=8, tilt=26):
    """One side of a laurel, from `a0` degrees to `a1` along a circle.

    Leaves sit on the arc and point along the tangent, tilted outward, shrinking
    toward the tip. The first attempt computed an angle and then never used it,
    placing the leaves as a broken ring around the word instead of two sprigs
    growing up from the foot — which is exactly the sort of thing that looks
    fine in the code and absurd on the page.
    """
    sign = 1 if a1 > a0 else -1
    for i in range(leaves):
        f = i / (leaves - 1)
        th = a0 + (a1 - a0) * f
        t = math.radians(th)
        _leaf(c, cx + r * math.cos(t), cy + r * math.sin(t),
              r * (0.50 - 0.19 * f), r * (0.23 - 0.09 * f),
              th + sign * (90 + tilt))


# ── the award mark, three candidates ────────────────────────────────────
# The first attempt at this was a fingerprint, on the reasoning that أثر is a
# trace and a fingerprint is the trace everybody recognises. Rendered, nested
# arcs open at the foot read as a wifi symbol and the core became a blob. The
# idea was right and the form was not, so these three use typography and
# classic seal language instead of illustration — none of them can look
# amateur, and all three survive being 14mm wide.

def mark_seal(c, cx, cy, r):
    """A struck seal carrying the word itself: أثر.

    What a seal has always done is carry a word. Setting it in the brand Arabic
    face means the mark cannot be badly drawn, reads at any size, and says
    exactly what the award is called.
    """
    c.saveState()
    c.setFillColor(YELLOW)
    c.circle(cx, cy, r, stroke=0, fill=1)
    _milled(c, cx, cy, r)
    c.setStrokeColor(BLACK)
    c.setLineWidth(r * 0.05)
    c.circle(cx, cy, r * 0.82, stroke=1, fill=0)
    c.setFillColor(BLACK)
    c.setFont(AR_BOLD, r * 0.82)
    c.drawCentredString(cx, cy - r * 0.30, ar('أثر'))
    c.restoreState()


def mark_medal(c, cx, cy, r):
    """A struck medal: an eight-point star on a milled disc.

    The plainest possible award signal. Nothing to misread.
    """
    c.saveState()
    c.setFillColor(YELLOW)
    c.circle(cx, cy, r, stroke=0, fill=1)
    _milled(c, cx, cy, r)
    c.setStrokeColor(BLACK)
    c.setLineWidth(r * 0.05)
    c.circle(cx, cy, r * 0.82, stroke=1, fill=0)
    c.setFillColor(BLACK)
    _star(c, cx, cy, r * 0.60)
    c.restoreState()


def mark_laurel(c, cx, cy, r):
    """أثر inside a laurel, no disc. The quietest of the three.

    Sits on white rather than on a yellow coin, so it can go next to text
    without shouting over it.
    """
    c.saveState()
    c.setFillColor(BLACK)
    # Open at the top, meeting at the foot, as a wreath does.
    _sprig(c, cx, cy, r * 0.86, 264, 158)
    _sprig(c, cx, cy, r * 0.86, 276, 382)
    c.setFont(AR_BOLD, r * 0.72)
    c.drawCentredString(cx, cy - r * 0.24, ar('أثر'))
    c.setStrokeColor(YELLOW)
    c.setLineWidth(r * 0.10)
    c.line(cx - r * 0.34, cy - r * 0.52, cx + r * 0.34, cy - r * 0.52)
    c.restoreState()


#: The award mark, by the key HR would pick from.
MARKS = {'seal': mark_seal, 'medal': mark_medal, 'laurel': mark_laurel}



def from_settings(nomination, settings=None):
    """A certificate for one nomination, using whatever HR has configured.

    The sheet itself is cert_simple.render_rule — centred, one yellow bar across
    the head, the Athar medal bottom-right, English throughout. Imported inside
    the function because cert_simple imports this module for its fonts, its
    colours and the wordmark.
    """
    from .cert_simple import render_rule
    from .models import TaqdeerSettings
    s = settings or TaqdeerSettings.get()
    image = None
    if s.signature_image:
        try:
            image = s.signature_image.path
        except (ValueError, NotImplementedError):
            image = None
    return render_rule(
        nomination.recipient_name, nomination.citation, nomination.cycle.label,
        signatory=s.signatory_title, signatory_name=s.signatory_name,
        title=s.certificate_title,
        recognition_en=s.certificate_recognition_line,
        signature_image=image)
