"""The certification announcement, as a picture.

HR types a name, a certification and a date; this draws the card. Same approach
as the Athar certificate — ReportLab to a PDF page, then pypdfium2 to a PNG —
so the two share their fonts, their palette and their logo, and a certification
card cannot drift away from the award it sits beside.

No per-certification logo is uploaded. The AJi wordmark goes on every one of
them, which is the point: these are announcements from the company, and asking
HR to find a PMI logo before they can congratulate somebody is the friction the
whole feature exists to remove.
"""
import io
import os

from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas

from .certificate import (AR_BODY, BLACK, BODY, BOLD, LOGO, LOGO_RATIO,
                         MOTTO_LINES, ORG,
                          WHITE, YELLOW, _fit, _fonts, _wrap, ar)

#: Square. A congratulations card, not a certificate — the certificate shape is
#: the Athar award's and the two should not be mistaken for each other. Square
#: also happens to be what every chat and feed crops to without cutting.
CW = CH = 190 * mm


def render(employee_name, certification, issuer='', obtained='',
           headline='Congratulations'):
    """The announcement card as PDF bytes."""
    _fonts()
    buf = io.BytesIO()
    c = pdfcanvas.Canvas(buf, pagesize=(CW, CH))
    c.setTitle(f'{employee_name} — {certification}')
    c.setAuthor('AJi Group')

    # White ground, one yellow bar down the left, matching the certificate.
    c.setFillColor(WHITE)
    c.rect(0, 0, CW, CH, stroke=0, fill=1)
    c.setFillColor(YELLOW)
    c.rect(0, 0, 7 * mm, CH, stroke=0, fill=1)

    left = 22 * mm
    right = CW - 22 * mm
    width = right - left
    mid = CW / 2

    # ── the wordmark, centred at the top ──
    lw = 30 * mm
    if os.path.exists(LOGO):
        c.drawImage(LOGO, mid - lw / 2, CH - 34 * mm, width=lw,
                    height=lw * LOGO_RATIO, mask='auto',
                    preserveAspectRatio=True, anchor='sw')

    c.setFillColor(BLACK)
    c.setFont(BOLD, 9.5)
    c.drawCentredString(mid, CH - 50 * mm, headline.upper())
    c.setFont(AR_BODY, 10.5)
    c.drawCentredString(mid, CH - 57 * mm, ar('مبارك'))

    c.setFillColor(YELLOW)
    c.rect(mid - 15 * mm, CH - 67 * mm, 30 * mm, 2.6 * mm, stroke=0, fill=1)

    # ── the news, centred ──
    c.setFillColor(BLACK)
    c.setFont(BODY, 11)
    c.drawCentredString(mid, CH - 84 * mm, 'We are proud to share that')

    size = _fit(c, employee_name, BOLD, 27, width)
    c.setFont(BOLD, size)
    c.drawCentredString(mid, CH - 102 * mm, employee_name)

    c.setFont(BODY, 11)
    c.drawCentredString(mid, CH - 116 * mm, 'has successfully obtained')

    lines = _wrap(c, certification, BOLD, 17, width)[:3]
    y = CH - 132 * mm
    for line in lines:
        c.setFont(BOLD, 17)
        c.drawCentredString(mid, y, line)
        y -= 8 * mm

    meta = ' · '.join(x for x in (issuer, obtained) if x)
    if meta:
        c.setFillColor(YELLOW)
        c.rect(mid - 12 * mm, y + 1 * mm, 24 * mm, 2.2 * mm, stroke=0, fill=1)
        c.setFillColor(BLACK)
        c.setFont(BODY, 10)
        c.drawCentredString(mid, y - 9 * mm, meta)

    # ── foot ──
    c.setStrokeColor(BLACK)
    c.setLineWidth(0.5)
    # Lifted clear of the motto: two stacked lines reach higher than the single
    # line this rule was placed for, and at 16mm the first of them sat on it.
    c.line(left, 19 * mm, right, 19 * mm)
    c.setFillColor(BLACK)
    # The motto leads and the company closes, the same way round as the
    # certificate footer — the two documents are seen side by side and read as
    # one set only if they sign off identically.
    #
    # Regular weight and the same tight leading. Centred on the company's
    # baseline rather than stacked up from it, because the rule above this band
    # is close and an upward stack ran the first line through it.
    c.setFont(BODY, 8.4)
    for i, line in enumerate(MOTTO_LINES):
        c.drawString(left, 10.5 * mm + (0.5 - i) * 3.5 * mm, line)
    c.setFont(BODY, 7.4)
    c.drawRightString(right, 10.5 * mm, ORG)

    c.showPage()
    c.save()
    return buf.getvalue()


def as_png(certification, dpi=170):
    """The card as a PNG, ready to post anywhere."""
    from .release_views import _as_png
    who = (certification.employee.get_full_name()
           or certification.employee.username)
    pdf = render(who, certification.name,
                 issuer=certification.issuer or '',
                 obtained=certification.obtained_on.strftime('%B %Y'))
    return _as_png(pdf, dpi=dpi)
