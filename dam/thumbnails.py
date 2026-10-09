# dam/thumbnails.py
"""Thumbnail generation, degrading honestly by what the host can actually do.

Images are rendered with Pillow and PDFs with pypdfium2, both plain pip
dependencies, so those two work everywhere the Hub runs. Office documents and
video still need external binaries (LibreOffice, ffmpeg) which are NOT installed
on this host, so they fall back to a typed icon rather than a broken image.

pypdfium2 was chosen over the usual pdf2image/Poppler pairing precisely because
it needs no system install: it ships a self-contained wheel under a permissive
licence, where Poppler would mean a server package and PyMuPDF would mean AGPL.
Poppler's pdftoppm is still used when present, since a host that has it can
render some malformed files pdfium rejects.

The binary lookups below are done at call time via shutil.which, so installing
any of those tools later starts producing thumbnails with no code change.

Deliberately NOT probed: ImageMagick's `convert`. On Windows a `convert.exe`
exists in system32 as the filesystem conversion tool, and calling it would be
both useless and dangerous.
"""
import io
import shutil
import subprocess
from pathlib import Path

from django.core.files.base import ContentFile

THUMB_MAX = (640, 640)
THUMB_QUALITY = 82


def tool_available(name):
    return shutil.which(name) is not None


def pdfium_available():
    try:
        import pypdfium2  # noqa: F401
    except Exception:
        return False
    return True


def capabilities():
    """What this host can currently thumbnail. Surfaced in the UI/tests."""
    return {
        'image': True,                            # Pillow is a project dependency
        'pdf':   pdfium_available() or tool_available('pdftoppm'),
        'office': tool_available('soffice') or tool_available('libreoffice'),
        'video': tool_available('ffmpeg'),
    }


def _save_image_bytes(asset, raw, suffix='jpg'):
    from PIL import Image
    try:
        with Image.open(io.BytesIO(raw)) as im:
            im = im.convert('RGB')
            im.thumbnail(THUMB_MAX)
            out = io.BytesIO()
            im.save(out, format='JPEG', quality=THUMB_QUALITY, optimize=True)
    except Exception:
        return False
    stem = Path(asset.filename).stem[:60] or 'thumb'
    asset.thumbnail.save(f'{stem}-{asset.pk}.{suffix}',
                         ContentFile(out.getvalue()), save=False)
    return True


def generate(asset):
    """Build and attach a thumbnail. Returns True when one was produced.

    Never raises: a library listing must not break because one file is odd.
    """
    if not asset.file:
        return False
    kind = asset.kind
    caps = capabilities()

    try:
        if kind == 'image' and asset.ext != '.svg':
            asset.file.open('rb')
            try:
                raw = asset.file.read()
            finally:
                asset.file.close()
            return _save_image_bytes(asset, raw)

        if kind == 'pdf' and caps['pdf']:
            # pdfium first because it needs no system install; pdftoppm is the
            # fallback for files pdfium refuses to open.
            return _via_pdfium(asset) or _via_pdftoppm(asset)

        if kind == 'video' and caps['video']:
            return _via_ffmpeg(asset)

        if kind in ('doc', 'sheet', 'slide') and caps['office']:
            return _via_soffice(asset)
    except Exception:
        return False

    return False


def _local_path(asset):
    """Absolute path on disk, or None for non-filesystem storage."""
    try:
        return Path(asset.file.path)
    except (NotImplementedError, ValueError):
        return None


def _via_pdfium(asset):
    """First page via pypdfium2. Works from bytes, so remote storage is fine."""
    if not pdfium_available():
        return False
    import pypdfium2 as pdfium
    asset.file.open('rb')
    try:
        raw = asset.file.read()
    finally:
        asset.file.close()
    doc = pdfium.PdfDocument(raw)
    try:
        if len(doc) == 0:
            return False
        page = doc[0]
        # 640px on the long edge, matching THUMB_MAX before Pillow downsizes.
        scale = THUMB_MAX[0] / max(page.get_width(), page.get_height(), 1)
        pil = page.render(scale=max(scale, 0.1)).to_pil()
        out = io.BytesIO()
        pil.convert('RGB').save(out, format='JPEG', quality=THUMB_QUALITY)
    finally:
        doc.close()
    return _save_image_bytes(asset, out.getvalue())


def _via_pdftoppm(asset):
    if not tool_available('pdftoppm'):
        return False
    src = _local_path(asset)
    if src is None:
        return False
    proc = subprocess.run(
        ['pdftoppm', '-jpeg', '-f', '1', '-l', '1', '-scale-to', '640', str(src), '-'],
        capture_output=True, timeout=25)
    if proc.returncode != 0 or not proc.stdout:
        return False
    return _save_image_bytes(asset, proc.stdout)


def _via_ffmpeg(asset):
    src = _local_path(asset)
    if src is None:
        return False
    proc = subprocess.run(
        ['ffmpeg', '-v', 'error', '-ss', '00:00:01', '-i', str(src),
         '-frames:v', '1', '-vf', 'scale=640:-1', '-f', 'image2pipe',
         '-vcodec', 'mjpeg', '-'],
        capture_output=True, timeout=30)
    if proc.returncode != 0 or not proc.stdout:
        return False
    return _save_image_bytes(asset, proc.stdout)


def _via_soffice(asset):
    """Render the first page via LibreOffice, then thumbnail that PDF."""
    src = _local_path(asset)
    if src is None or not caps_pdf():
        return False
    binary = shutil.which('soffice') or shutil.which('libreoffice')
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        proc = subprocess.run(
            [binary, '--headless', '--convert-to', 'pdf', '--outdir', tmp, str(src)],
            capture_output=True, timeout=90)
        if proc.returncode != 0:
            return False
        pdfs = list(Path(tmp).glob('*.pdf'))
        if not pdfs:
            return False
        raw = _first_page_jpeg(pdfs[0])
        return _save_image_bytes(asset, raw) if raw else False


def _first_page_jpeg(path):
    """First page of a PDF on disk as JPEG bytes, by whichever route works."""
    if pdfium_available():
        import pypdfium2 as pdfium
        try:
            doc = pdfium.PdfDocument(str(path))
            try:
                if len(doc):
                    page = doc[0]
                    scale = THUMB_MAX[0] / max(page.get_width(), page.get_height(), 1)
                    out = io.BytesIO()
                    page.render(scale=max(scale, 0.1)).to_pil().convert('RGB').save(
                        out, format='JPEG', quality=THUMB_QUALITY)
                    return out.getvalue()
            finally:
                doc.close()
        except Exception:
            pass
    if tool_available('pdftoppm'):
        proc = subprocess.run(
            ['pdftoppm', '-jpeg', '-f', '1', '-l', '1', '-scale-to', '640',
             str(path), '-'], capture_output=True, timeout=25)
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
    return None


def caps_pdf():
    return pdfium_available() or tool_available('pdftoppm')
