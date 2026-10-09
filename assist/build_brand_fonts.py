"""Convert AJi's brand fonts from OpenType/CFF to TrueType, once.

The brand kit ships Draft B and IBM Plex Sans Arabic as .otf with PostScript
(CFF) outlines. ReportLab's TTFont embedder only reads TrueType `glyf` outlines
and refuses a CFF font outright, so the branded PDF cannot use the brand fonts
as shipped. This converts the curves (cubic to quadratic) and writes .ttf files
into static/fonts/, which is where the PDF exporter looks.

Run it when the brand fonts change, not per request:

    python -m assist.build_brand_fonts

It reads the .otf files the site already ships in static/fonts/ for its own
webfonts, so the conversion is reproducible from the repository alone. Pass a
directory to read them from somewhere else. The .ttf output is committed, so a
deployment never converts anything at runtime.
"""
import os
import sys

from fontTools.ttLib import TTFont, newTable
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.pens.cu2quPen import Cu2QuPen

# Draft B is the display face and carries no Arabic at all; IBM Plex Sans Arabic
# covers Arabic and Latin both, and doubles as the complex-script face.
WANTED = [
    ('draft_b_regular.otf', 'DraftB-Regular.ttf'),
    ('draft_b_bold.otf', 'DraftB-Bold.ttf'),
    ('IBM_Plex_Sans_Arabic_Regular.otf', 'PlexArabic-Regular.ttf'),
    ('IBM_Plex_Sans_Arabic_Bold.otf', 'PlexArabic-Bold.ttf'),
]

# Tolerance for the cubic-to-quadratic fit, in font units. 1.0 at a 1000-unit em
# is far below anything visible in print.
MAX_ERR = 1.0


def otf_to_ttf(src, dest):
    font = TTFont(src)
    if 'glyf' in font:
        raise SystemExit(f'{src} is already TrueType-flavoured')

    glyph_set = font.getGlyphSet()
    upem = font['head'].unitsPerEm
    glyf = newTable('glyf')
    glyf.glyphOrder = font.getGlyphOrder()
    glyf.glyphs = {}

    for name in glyph_set.keys():
        pen = TTGlyphPen(glyph_set)
        # Cu2QuPen turns the CFF cubics into the quadratics glyf can hold.
        glyph_set[name].draw(Cu2QuPen(pen, MAX_ERR * upem / 1000.0))
        glyf.glyphs[name] = pen.glyph()

    # Each glyph has to know its own bounding box before maxp can be recalculated
    # from them, and nothing has computed one yet — the pen only drew outlines.
    for glyph in glyf.glyphs.values():
        glyph.recalcBounds(glyf)

    font['glyf'] = glyf
    font['loca'] = newTable('loca')

    # maxp has to describe TrueType outlines, and the CFF tables must go or the
    # result claims to be both flavours at once.
    maxp = font['maxp']
    maxp.tableVersion = 0x00010000
    maxp.numGlyphs = len(glyf.glyphs)
    for attr, value in (('maxPoints', 0), ('maxContours', 0),
                        ('maxCompositePoints', 0), ('maxCompositeContours', 0),
                        ('maxZones', 1), ('maxTwilightPoints', 0),
                        ('maxStorage', 0), ('maxFunctionDefs', 0),
                        ('maxInstructionDefs', 0), ('maxStackElements', 0),
                        ('maxSizeOfInstructions', 0), ('maxComponentElements', 0),
                        ('maxComponentDepth', 0)):
        setattr(maxp, attr, value)
    maxp.recalc(font)

    font['head'].indexToLocFormat = 0
    for drop in ('CFF ', 'VORG'):
        if drop in font:
            del font[drop]

    # The sfnt version tag is what a reader trusts about which outline flavour
    # this is. Leaving it as 'OTTO' means ReportLab rejects the file for
    # "postscript outlines" no matter that glyf is the only outline table left.
    font.sfntVersion = '\000\001\000\000'

    font.save(dest)
    font.close()
    return os.path.getsize(dest)


def main():
    out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'static', 'fonts')
    src_dir = sys.argv[1] if len(sys.argv) > 1 else out_dir
    os.makedirs(out_dir, exist_ok=True)

    for src_name, out_name in WANTED:
        src = os.path.join(src_dir, src_name)
        if not os.path.exists(src):
            print(f'  MISSING {src_name} — skipped')
            continue
        size = otf_to_ttf(src, os.path.join(out_dir, out_name))
        print(f'  {src_name}  ->  {out_name}  ({size // 1024} KB)')


if __name__ == '__main__':
    main()
