"""Reading a spreadsheet or a Word document without downloading it.

The viewer could open a PDF and an image and nothing else, so every .xlsx and
.docx in the Hub was a file you had to save to your machine before you could
find out whether it was the one you wanted. On a read-only library you could not
even do that.

These are rendered to HTML rather than to pictures of pages, because a
spreadsheet has no pages — it has sheets, and its columns are wider than any
paper. HTML also keeps the text selectable and searchable with the browser's own
Find, which a rasterised page never is.

The view-only rule survives intact, and for the same reason it does for a PDF:
what crosses the network is our own markup built from the file's *values*, never
the file. Everything from the document is escaped on the way out and the only
tags that appear are ones written here — nothing from the upload is ever passed
through as markup. That matters more than usual: an .xlsx is a zip somebody
uploaded, and a cell can contain anything a person can type.

Not supported, deliberately rather than by omission:

* **.doc and .xls**, the pre-2007 binary formats. openpyxl and python-docx read
  the zipped XML formats only, and there is no reader for the old ones in this
  project's dependencies. `kind_of` does not claim them, so the viewer says the
  document cannot be previewed instead of showing an empty one.
* **.pptx**, which would need python-pptx.
* **Merged cells and cell colours.** openpyxl's read-only mode, which is what
  makes a large workbook openable at all, does not report them.
"""
import datetime
import logging
from pathlib import Path

from django.utils.html import escape

logger = logging.getLogger(__name__)

#: Bounds on one rendered sheet. A workbook can hold a million rows; a browser
#: given a million table rows stops responding, and nobody checking whether they
#: have the right file reads past the first screenful anyway. Truncation is
#: always said out loud rather than left to look like the end of the data.
MAX_ROWS = 600
MAX_COLS = 40

#: Bounds on one rendered document, for the same reason.
MAX_BLOCKS = 3000

#: Images are inlined as data URIs, so their bytes land in the page. One large
#: photograph is worth showing; a hundred of them is a page nobody can load.
MAX_IMAGE = 1_500_000
MAX_IMAGES_TOTAL = 8_000_000

SHEET_SUFFIXES = {'.xlsx', '.xlsm'}
WORD_SUFFIXES = {'.docx'}


def kind(name):
    """'sheet', 'doc', or '' for anything this module cannot read."""
    suffix = Path(name or '').suffix.lower()
    if suffix in SHEET_SUFFIXES:
        return 'sheet'
    if suffix in WORD_SUFFIXES:
        return 'doc'
    return ''


# ── spreadsheets ─────────────────────────────────────────────────────────────

def sheet_names(file_field):
    """The visible sheets, in order. Empty when the file cannot be opened.

    Hidden sheets are left out: they are hidden in Excel too, and a reader
    counting "3 of 7" against a workbook that shows three tabs would be right to
    think something was wrong.
    """
    try:
        import openpyxl
    except ImportError:                                        # pragma: no cover
        logger.warning('[office] openpyxl is not installed')
        return []
    try:
        with file_field.open('rb') as handle:
            book = openpyxl.load_workbook(handle, read_only=True, data_only=True)
            try:
                return [ws.title for ws in book.worksheets
                        if ws.sheet_state == 'visible']
            finally:
                book.close()
    except Exception:                                              # noqa: BLE001
        logger.warning('[office] could not open %s', file_field.name, exc_info=True)
        return []


def render_sheet(file_field, index):
    """One worksheet as an HTML table, or None.

    Column letters and row numbers are drawn as well as the values. Without them
    a table of numbers is unmoored — somebody reading a formula that mentions D7
    has no way to find D7.
    """
    try:
        import openpyxl
        from openpyxl.utils import get_column_letter
    except ImportError:                                        # pragma: no cover
        return None

    try:
        with file_field.open('rb') as handle:
            book = openpyxl.load_workbook(handle, read_only=True, data_only=True)
            try:
                sheets = [ws for ws in book.worksheets
                          if ws.sheet_state == 'visible']
                if index < 0 or index >= len(sheets):
                    return None
                sheet = sheets[index]
                rows = []
                for row in sheet.iter_rows(max_row=MAX_ROWS + 1,
                                           max_col=MAX_COLS + 1):
                    rows.append([_cell(c) for c in row])
                title = sheet.title
            finally:
                book.close()
    except Exception:                                              # noqa: BLE001
        logger.warning('[office] sheet %s of %s failed', index, file_field.name,
                       exc_info=True)
        return None

    # Trailing blank rows and columns are an artefact of wherever somebody last
    # put the cursor, not content, and rendering them draws a table mostly made
    # of nothing.
    while rows and not any(v for v in rows[-1]):
        rows.pop()
    width = max((len(r) for r in rows), default=0)
    while width and all(len(r) < width or not r[width - 1] for r in rows):
        width -= 1

    if not rows or not width:
        return _wrap('sheet', title, '<p class="ofc-empty">This sheet is empty.</p>')

    clipped_rows = len(rows) > MAX_ROWS
    clipped_cols = width > MAX_COLS
    rows = rows[:MAX_ROWS]
    width = min(width, MAX_COLS)

    # The first row becomes headings only when every cell in it is text — the
    # common shape of a business sheet. A row of numbers is data, and promoting
    # it would hide the sheet's first record inside the header.
    heads = bool(rows) and all(
        isinstance(v, str) and v.strip() for v in rows[0][:width])

    out = ['<div class="ofc-scroll"><table class="ofc-grid">']
    out.append('<thead><tr><th class="ofc-corner"></th>')
    for c in range(width):
        out.append(f'<th class="ofc-col">{get_column_letter(c + 1)}</th>')
    out.append('</tr></thead><tbody>')
    for r, row in enumerate(rows):
        out.append(f'<tr><th class="ofc-rownum">{r + 1}</th>')
        for c in range(width):
            value = row[c] if c < len(row) else ''
            tag = 'th' if (heads and r == 0) else 'td'
            num = ' class="ofc-num"' if _looks_numeric(value) else ''
            out.append(f'<{tag}{num}>{escape(value)}</{tag}>')
        out.append('</tr>')
    out.append('</tbody></table></div>')

    if clipped_rows or clipped_cols:
        bits = []
        if clipped_rows:
            bits.append(f'the first {MAX_ROWS} rows')
        if clipped_cols:
            bits.append(f'the first {MAX_COLS} columns')
        out.append(f'<p class="ofc-clip">Showing {" and ".join(bits)}. '
                   f'Save the file to see all of it.</p>')
    return _wrap('sheet', title, ''.join(out))


def _cell(cell):
    """One cell as the text a person would see in Excel."""
    value = cell.value
    if value is None:
        return ''
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    if isinstance(value, datetime.datetime):
        if value.hour or value.minute or value.second:
            return value.strftime('%Y-%m-%d %H:%M')
        return value.strftime('%Y-%m-%d')
    if isinstance(value, datetime.date):
        return value.strftime('%Y-%m-%d')
    if isinstance(value, datetime.time):
        return value.strftime('%H:%M')
    if isinstance(value, (int, float)):
        fmt = getattr(cell, 'number_format', '') or ''
        # A percentage is stored as the fraction, so 0.15 in a cell formatted as
        # a percentage reads as 0.15 unless it is put back into the form the
        # sheet displays it in.
        if '%' in fmt:
            return _trim(float(value) * 100) + '%'
        return _trim(value)
    return str(value)


def _trim(value):
    """A number without the trailing zeros a float turns into."""
    if isinstance(value, int) or float(value).is_integer():
        return f'{int(value):,}'
    return f'{value:,.4f}'.rstrip('0').rstrip('.')


def _looks_numeric(text):
    if not text:
        return False
    return text.replace(',', '').replace('%', '').replace('-', '') \
               .replace('.', '').isdigit()


# ── Word documents ───────────────────────────────────────────────────────────

def render_doc(file_field):
    """A whole .docx as HTML, or None.

    One page, not several: a .docx has no fixed pagination — where the page
    breaks fall is decided when it is laid out for a particular paper size, and
    nothing here lays it out.
    """
    try:
        import docx
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError:                                        # pragma: no cover
        logger.warning('[office] python-docx is not installed')
        return None

    try:
        with file_field.open('rb') as handle:
            document = docx.Document(handle)
            # Walked in document order. `document.paragraphs` and
            # `document.tables` are two separate lists, so building from them
            # puts every table at the end — a form whose fields sat between its
            # instructions came out as the instructions followed by the fields.
            blocks = []
            for child in document.element.body.iterchildren():
                if child.tag == qn('w:p'):
                    blocks.append(Paragraph(child, document))
                elif child.tag == qn('w:tbl'):
                    blocks.append(Table(child, document))
                if len(blocks) > MAX_BLOCKS:
                    break
            budget = [MAX_IMAGES_TOTAL]
            html = _blocks_html(blocks, document.part, budget)
    except Exception:                                              # noqa: BLE001
        logger.warning('[office] could not read %s', file_field.name, exc_info=True)
        return None

    if not html.strip():
        html = '<p class="ofc-empty">This document has no text in it.</p>'
    # No label: the viewer's own bar already carries the document's title, and a
    # second line underneath repeating the filename with its underscores in is
    # noise. A sheet does get one, because the sheet's *name* is not the file's.
    return _wrap('doc', '', f'<div class="ofc-page">{html}</div>')


def _blocks_html(blocks, part, budget):
    """Paragraphs, lists and tables, in the order they appear."""
    from docx.table import Table

    out = []
    in_list = False
    for block in blocks:
        if isinstance(block, Table):
            if in_list:
                out.append('</ul>')
                in_list = False
            out.append(_table_html(block, part, budget))
            continue

        body = _runs_html(block, part, budget)
        style = (getattr(block.style, 'name', '') or '')
        listy = _is_list(block, style)

        if listy and not body:
            continue
        if listy:
            if not in_list:
                out.append('<ul class="ofc-list">')
                in_list = True
            out.append(f'<li>{body}</li>')
            continue
        if in_list:
            out.append('</ul>')
            in_list = False

        if not body:
            continue
        level = _heading_level(style)
        if level:
            # Starting at h3: h1 is the page's own title and h2 belongs to the
            # panels around it, so a document's own headings go below both.
            out.append(f'<h{level}>{body}</h{level}>')
        else:
            out.append(f'<p>{body}</p>')
    if in_list:
        out.append('</ul>')
    return ''.join(out)


def _is_list(paragraph, style):
    if 'list' in style.lower():
        return True
    try:
        return paragraph._p.pPr is not None and paragraph._p.pPr.numPr is not None
    except AttributeError:
        return False


def _heading_level(style):
    """h3..h6 for Heading 1..4, and h6 for anything deeper."""
    name = style.lower()
    if name in ('title', 'subtitle'):
        return 3
    if not name.startswith('heading'):
        return 0
    digits = ''.join(ch for ch in name if ch.isdigit())
    depth = int(digits) if digits else 1
    return min(3 + depth - 1, 6)


def _runs_html(paragraph, part, budget):
    """One paragraph's runs, with bold and italic kept, plus inline pictures."""
    from docx.oxml.ns import qn

    out = []
    for run in paragraph.runs:
        for blip in run._element.findall('.//' + qn('a:blip')):
            img = _image_html(blip, part, budget)
            if img:
                out.append(img)
        text = escape(run.text or '')
        if not text.strip():
            out.append(text)
            continue
        if run.bold:
            text = f'<strong>{text}</strong>'
        if run.italic:
            text = f'<em>{text}</em>'
        if run.underline:
            text = f'<u>{text}</u>'
        out.append(text)
    return ''.join(out).strip()


def _image_html(blip, part, budget):
    """An embedded picture as a data URI, within the page's byte budget."""
    from base64 import b64encode
    from docx.oxml.ns import qn

    rid = blip.get(qn('r:embed'))
    if not rid:
        return ''
    try:
        image_part = part.related_parts[rid]
        blob = image_part.blob
    except (KeyError, AttributeError):
        return ''
    if len(blob) > MAX_IMAGE or len(blob) > budget[0]:
        # Named rather than dropped: a form whose only content is a picture
        # would otherwise preview as an empty document.
        return '<span class="ofc-noimg">[picture too large to preview]</span>'
    budget[0] -= len(blob)
    mime = getattr(image_part, 'content_type', '') or 'image/png'
    data = b64encode(blob).decode('ascii')
    return f'<img class="ofc-img" alt="" src="data:{escape(mime)};base64,{data}">'


def _table_html(table, part, budget):
    out = ['<div class="ofc-scroll"><table class="ofc-table">']
    for r, row in enumerate(table.rows):
        out.append('<tr>')
        for cell, span in _row_cells(row):
            # A cell holds paragraphs of its own, so it goes through the same
            # renderer — which is what keeps a bulleted list inside a table cell
            # looking like one.
            inner = ''.join(f'<p>{_runs_html(p, part, budget)}</p>'
                            for p in cell.paragraphs
                            if _runs_html(p, part, budget))
            tag = 'th' if r == 0 else 'td'
            wide = f' colspan="{span}"' if span > 1 else ''
            out.append(f'<{tag}{wide}>{inner}</{tag}>')
        out.append('</tr>')
    out.append('</table></div>')
    return ''.join(out)


def _row_cells(row):
    """(cell, colspan) across one row, with merged cells emitted once.

    python-docx returns a merged cell once per grid column it covers, so a
    heading spanning three columns came out as the same words printed three times
    — a job description with two merged columns rendered as five, with
    "Employee Name:" twice. Consecutive entries backed by the same underlying
    element are one cell with a colspan.

    Vertical merges are not handled: the same cell is returned in each row it
    covers, and there is no rowspan here. They repeat instead, which is wrong but
    legible; guessing at rowspans from a flat list gets tables badly out of shape.
    """
    out = []
    for cell in row.cells:
        if out and out[-1][0]._tc is cell._tc:
            out[-1][1] += 1
        else:
            out.append([cell, 1])
    return [(cell, span) for cell, span in out]


def _wrap(what, label, inner):
    head = f'<div class="ofc-label">{escape(label)}</div>' if label else ''
    return f'<div class="ofc ofc-{what}">{head}{inner}</div>'
