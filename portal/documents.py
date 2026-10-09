# portal/documents.py
"""Serving controlled documents, and rendering their pages.

Everything under MEDIA_URL is served straight off disk by the URL conf, with no
check of any kind — an anonymous request for a policy PDF returns 200 today.
That makes "only this group may see it" undeliverable by adding a field: the
page would hide the link and the file would still answer to anyone who had the
address. So a restricted document is never linked to its media URL. It is read
through the views here, which ask first.

**View-only means the file is not sent.** A download button that is merely
hidden is a suggestion; the bytes are still one request away. Instead the pages
are rasterised on the server with pypdfium2 and the reader is shown pictures of
them. What crosses the network is a PNG per page and never the PDF, so there is
nothing to save but a screenshot — which is the honest limit of view-only
anywhere, and the point at which the answer stops being technical.

Renders are cached under BASE_DIR/.cache, deliberately *not* under MEDIA_ROOT:
caching a restricted document into the one directory that is served without a
permission check would hand out exactly what this file exists to protect.
"""
import hashlib
import logging
import mimetypes
from pathlib import Path

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse, JsonResponse

logger = logging.getLogger(__name__)

#: Wide enough to read on a laptop without being a photograph of a page.
PAGE_WIDTH = 1400
#: A guard against a hand-typed page number turning into a very long render.
MAX_PAGES = 400

IMAGE_SUFFIXES = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}
PDF_SUFFIXES = {'.pdf'}
#: Rendered to HTML rather than to pictures — see portal/office.py. A workbook's
#: "pages" are its worksheets; a Word document is one page, because a .docx has
#: no pagination until something lays it out for a paper size.
OFFICE_KINDS = {'sheet', 'doc'}


#: What a file is, in a mark and a word. The extension in capitals is the label
#: because it is what the reader already recognises — "XLSX" needs no learning,
#: and "Microsoft Excel Worksheet" does not fit under a filename. The longer name
#: goes in the title attribute for anybody who wants it.
#: Kept beside kind_of so there is one place that knows about file types; the
#: previewer and the label cannot then disagree about what a .docx is.
FORMATS = {
    '.pdf':  ('file-pdf',   'PDF document'),
    '.docx': ('file-doc',   'Word document'),
    '.doc':  ('file-doc',   'Word document (old format)'),
    '.rtf':  ('file-doc',   'Rich text document'),
    '.xlsx': ('file-xls',   'Excel workbook'),
    '.xlsm': ('file-xls',   'Excel workbook with macros'),
    '.xls':  ('file-xls',   'Excel workbook (old format)'),
    '.csv':  ('file-csv',   'Comma-separated values'),
    '.pptx': ('file-ppt',   'PowerPoint presentation'),
    '.ppt':  ('file-ppt',   'PowerPoint presentation (old format)'),
    '.png':  ('file-image', 'PNG image'),
    '.jpg':  ('file-image', 'JPEG image'),
    '.jpeg': ('file-image', 'JPEG image'),
    '.gif':  ('file-image', 'GIF image'),
    '.webp': ('file-image', 'WebP image'),
    '.bmp':  ('file-image', 'Bitmap image'),
    '.svg':  ('file-image', 'SVG image'),
    '.zip':  ('file-zip',   'Zip archive'),
    '.7z':   ('file-zip',   'Archive'),
    '.rar':  ('file-zip',   'Archive'),
    '.txt':  ('file-text',  'Plain text'),
    '.md':   ('file-text',  'Markdown text'),
    '.dwg':  ('file-code',  'AutoCAD drawing'),
    '.dxf':  ('file-code',  'CAD exchange drawing'),
}


def format_of(name):
    """What kind of file this is, for showing rather than for opening.

    Returns {'ext', 'icon', 'label', 'previewable'}. `ext` is upper-case and
    without its dot; an unknown extension keeps its own name rather than being
    called "file", because "DWG" tells a reader more than a generic word does.
    """
    suffix = Path(name or '').suffix.lower()
    icon, label = FORMATS.get(suffix, ('file-blank', 'File'))
    return {
        'ext': suffix.lstrip('.').upper() or 'FILE',
        'icon': icon,
        'label': label,
        # Whether pressing Read will show anything. Same call the viewer makes,
        # so a row cannot promise a preview the viewer then refuses.
        'previewable': bool(kind_of(name)),
    }


def cache_root():
    path = Path(settings.BASE_DIR) / '.cache' / 'docpages'
    path.mkdir(parents=True, exist_ok=True)
    return path


def kind_of(name):
    """'pdf', 'image', 'sheet', 'doc' or '' — what the viewer should do.

    '' is the answer for anything that cannot be shown, which the viewer reads
    as "offer the download instead". That includes .doc and .xls: the readers
    here handle the zipped XML formats only, and claiming the old binary ones
    would open an empty viewer rather than saying so.
    """
    suffix = Path(name or '').suffix.lower()
    if suffix in PDF_SUFFIXES:
        return 'pdf'
    if suffix in IMAGE_SUFFIXES:
        return 'image'
    from . import office
    return office.kind(name)


def page_count(file_field):
    """How many pages the viewer can show. Never raises.

    A PDF's pages, a workbook's visible sheets, or 1 for a single-page thing.
    0 means nothing can be shown, and the viewer offers the file instead.
    """
    kind = kind_of(file_field.name)
    if kind == 'sheet':
        from . import office
        return len(office.sheet_names(file_field))
    if kind != 'pdf':
        return 1
    try:
        import pypdfium2 as pdfium
    except ImportError:
        logger.warning('[documents] pypdfium2 is not installed; cannot page PDFs')
        return 0
    try:
        with file_field.open('rb') as handle:
            doc = pdfium.PdfDocument(handle.read())
            return min(len(doc), MAX_PAGES)
    except Exception:                                              # noqa: BLE001
        # A corrupt or password-protected file is a document that cannot be
        # previewed, not a page that should 500.
        logger.warning('[documents] could not read %s', file_field.name, exc_info=True)
        return 0


def _cache_path(file_field, index, width):
    """A cache name that changes when the file does.

    Keyed on the storage path, size and modification time rather than the
    primary key: replacing the attachment on a policy keeps its id, and a cache
    keyed on the id alone would go on serving pictures of the old document.
    """
    try:
        stamp = f'{file_field.size}-{int(Path(file_field.path).stat().st_mtime)}'
    except (OSError, ValueError, NotImplementedError):
        stamp = 'nostat'
    key = hashlib.sha1(f'{file_field.name}|{stamp}|{width}|{index}'.encode()).hexdigest()
    return cache_root() / f'{key}.png'


def render_page(file_field, index, width=PAGE_WIDTH):
    """One page of a PDF as PNG bytes, rendered once and kept.

    Returns None when the page cannot be produced, which the caller turns into
    a 404 rather than a broken image.
    """
    # Only a PDF is rasterisable. Without this a .docx reached pdfium, which
    # rejected it with a data-format error and a full traceback in the log —
    # once per page request, for a file that was never going to render.
    if kind_of(file_field.name) != 'pdf':
        return None

    cached = _cache_path(file_field, index, width)
    if cached.exists():
        try:
            return cached.read_bytes()
        except OSError:
            pass

    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None

    try:
        with file_field.open('rb') as handle:
            doc = pdfium.PdfDocument(handle.read())
            if index < 0 or index >= len(doc):
                return None
            page = doc[index]
            # Scale from the page's own width so every page comes out the same
            # size on screen whatever paper it was set on.
            scale = width / max(1.0, page.get_width())
            image = page.render(scale=min(scale, 4.0)).to_pil()
    except Exception:                                              # noqa: BLE001
        logger.warning('[documents] render failed for %s page %s',
                       file_field.name, index, exc_info=True)
        return None

    from io import BytesIO
    buffer = BytesIO()
    image.save(buffer, format='PNG', optimize=True)
    data = buffer.getvalue()
    try:
        cached.write_bytes(data)
    except OSError:
        pass                       # an unwritable cache is slow, not broken
    return data


def html_response(markup):
    """A rendered sheet or document.

    The markup is built in portal/office.py out of escaped values and tags
    written there — nothing from the uploaded file passes through as markup — so
    the viewer can put it straight into the page.
    """
    response = HttpResponse(markup, content_type='text/html; charset=utf-8')
    # Private for the same reason a rendered page is: this is the content of a
    # document somebody was individually allowed to read.
    response['Cache-Control'] = 'private, max-age=3600'
    return response


def page_response(file_field, index):
    """The one page the viewer asked for, in whatever form suits the file.

    Both page routes used to carry their own copy of this dispatch, which is how
    one of them could learn about a new format and the other not.
    """
    kind = kind_of(file_field.name)
    if kind == 'image':
        # An image is already a picture of itself; rasterising it again would
        # only cost quality.
        return file_response(file_field, download=False)
    if kind in OFFICE_KINDS:
        from . import office
        markup = (office.render_sheet(file_field, index) if kind == 'sheet'
                  else office.render_doc(file_field))
        if markup is None:
            raise Http404
        return html_response(markup)
    data = render_page(file_field, index)
    if data is None:
        raise Http404
    return png_response(data)


def png_response(data):
    response = HttpResponse(data, content_type='image/png')
    # Private: this is a page of a document somebody was individually allowed to
    # see, and a shared proxy holding it would undo that.
    response['Cache-Control'] = 'private, max-age=3600'
    return response


def file_response(file_field, download, filename=None):
    """The original file — inline to read, or as an attachment to keep."""
    try:
        handle = file_field.open('rb')
    except (OSError, ValueError):
        raise Http404
    name = filename or Path(file_field.name).name
    guessed = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    response = FileResponse(handle, content_type=guessed)
    disposition = 'attachment' if download else 'inline'
    response['Content-Disposition'] = f'{disposition}; filename="{name}"'
    response['Cache-Control'] = 'private, max-age=600'
    return response


def viewer_payload(request, doc_url, title, file_field, may_download,
                   subtitle='', download_url=''):
    """What the viewer needs to open a document.

    `pages` is 0 when the file cannot be rasterised — the viewer falls back to
    offering the file itself, or says so if it may not.
    """
    kind = kind_of(file_field.name)
    # 0 for anything the viewer cannot show — a .doc, a zip. The viewer reads
    # that as "offer the download instead", where 1 sent it to fetch a page that
    # was always going to 404.
    if kind in ('pdf', 'sheet'):
        pages = page_count(file_field)
    else:
        pages = 1 if kind in ('image', 'doc') else 0
    # A workbook's pages have names, and "Summary" is a great deal more use at
    # the top of the viewer than "2 / 7".
    labels = []
    if kind == 'sheet':
        from . import office
        labels = office.sheet_names(file_field)
    return JsonResponse({
        'title': title,
        'subtitle': subtitle,
        'kind': kind,
        'pages': pages,
        'labels': labels,
        # Whether the page endpoint answers with a picture or with markup. The
        # viewer has to know before it asks.
        'as_html': kind in OFFICE_KINDS,
        'page_url': doc_url,
        'download_url': download_url if may_download else '',
        'may_download': bool(may_download),
        'filename': Path(file_field.name).name,
    })
