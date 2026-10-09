"""Upgrade .doc and .xls documents to .docx and .xlsx so they can be previewed.

The Hub previews a spreadsheet or a Word document by reading it with openpyxl or
python-docx, and both read the zipped XML formats only. The pre-2007 binary
formats have no reader here, so a .doc shows "this document cannot be previewed"
and the only way to find out what is in it is to download it. One folder of job
descriptions held four files and all four were .doc, which made the whole
feature look broken.

Rather than add a runtime dependency for a format nobody has saved in since 2007,
this converts the files once, in place, using the copy of Word and Excel already
installed on this machine. Nothing the Hub serves depends on Office — this
command does, and it is run by hand.

    python manage.py convert_legacy_office --dry-run
    python manage.py convert_legacy_office

**The original is kept** next to the new file unless --replace is passed, and the
document record is only repointed once the new file exists and is readable by the
previewer that was the whole point. A conversion that produces a file the Hub
still cannot read is left alone and reported, not quietly committed.

Requires pywin32 and a desktop session — Office automation will not run headless.
"""
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from portal.models import Policy

#: The two Office "save as" constants. Named rather than inlined because 12 and
#: 51 are meaningless at the call site and easy to transpose.
WD_FORMAT_DOCX = 12
XL_FORMAT_XLSX = 51

UPGRADES = {'.doc': '.docx', '.xls': '.xlsx'}


class Command(BaseCommand):
    help = 'Convert .doc and .xls documents to .docx and .xlsx so they preview.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='List what would be converted and change nothing.')
        parser.add_argument('--replace', action='store_true',
                            help='Delete the original file once the new one reads. '
                                 'Off by default: the original is the only copy.')
        parser.add_argument('--limit', type=int, default=0,
                            help='Stop after this many, for a cautious first run.')

    def handle(self, *args, **options):
        jobs = []
        for policy in Policy.objects.exclude(file='').select_related('category'):
            if not policy.file:
                continue
            ext = Path(policy.file.name).suffix.lower()
            if ext in UPGRADES:
                jobs.append((policy, ext, UPGRADES[ext]))

        if not jobs:
            self.stdout.write('Nothing to do — no .doc or .xls documents.')
            return

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'{len(jobs)} document(s) in a format the previewer cannot read'))
        for policy, ext, target in jobs:
            where = policy.category.name if policy.category else 'no library'
            self.stdout.write(f'  {ext} → {target}  {policy.title[:52]}   [{where}]')

        if options['dry_run']:
            self.stdout.write(self.style.NOTICE('Dry run — nothing changed.'))
            return

        try:
            import pythoncom                                       # noqa: F401
            import win32com.client
        except ImportError as exc:
            raise CommandError(
                'pywin32 is needed to drive Word and Excel: pip install pywin32'
            ) from exc

        if options['limit']:
            jobs = jobs[:options['limit']]

        apps = _Office(win32com.client, self.stdout, self.style)
        done = failed = 0
        try:
            for policy, ext, target in jobs:
                if self._convert(policy, ext, target, apps, options['replace']):
                    done += 1
                else:
                    failed += 1
        finally:
            apps.quit()

        self.stdout.write(self.style.SUCCESS(f'Converted {done} document(s).'))
        if failed:
            self.stdout.write(self.style.WARNING(
                f'{failed} could not be converted and were left as they were.'))
        left = sum(1 for p in Policy.objects.exclude(file='')
                   if p.file and Path(p.file.name).suffix.lower() in UPGRADES)
        self.stdout.write(f'{left} document(s) still in an unreadable format.')

    # ── one document ────────────────────────────────────────────────────────

    def _convert(self, policy, ext, target, apps, replace):
        try:
            source = Path(policy.file.path)
        except (ValueError, NotImplementedError):
            self.stdout.write(self.style.WARNING(
                f'  no local path: {policy.title[:50]}'))
            return False
        if not source.is_file():
            self.stdout.write(self.style.WARNING(
                f'  file missing: {policy.title[:50]}'))
            return False

        # Never overwrite something already sitting there — a .docx beside a .doc
        # of the same name is somebody's own converted copy, not ours to clobber.
        new_path = source.with_suffix(target)
        n = 1
        while new_path.exists():
            new_path = source.with_name(f'{source.stem}_conv{n}{target}')
            n += 1

        try:
            if ext == '.doc':
                apps.word_convert(source, new_path)
            else:
                apps.excel_convert(source, new_path)
        except Exception as exc:                                   # noqa: BLE001
            self.stdout.write(self.style.WARNING(
                f'  Office refused {source.name}: {exc.__class__.__name__}'))
            return False

        if not new_path.is_file() or new_path.stat().st_size == 0:
            self.stdout.write(self.style.WARNING(
                f'  produced nothing for {source.name}'))
            return False

        # The point of the exercise is that it previews. If the new file cannot
        # be read by the previewer either, converting it achieved nothing and
        # repointing the record would only hide the problem.
        if not self._previewable(new_path, target):
            self.stdout.write(self.style.WARNING(
                f'  converted but still unreadable: {source.name}'))
            new_path.unlink(missing_ok=True)
            return False

        old_name = policy.file.name
        new_name = str(Path(old_name).with_name(new_path.name)).replace('\\', '/')
        with transaction.atomic():
            policy.file.name = new_name
            policy.save(update_fields=['file'])

        note = ''
        if replace:
            try:
                source.unlink()
                note = ' (original removed)'
            except OSError as exc:
                note = f' (original kept — {exc.strerror})'
        self.stdout.write(f'  ok  {source.name} → {new_path.name}{note}')
        return True

    def _previewable(self, path, target):
        """Can the previewer actually read what Office just wrote?"""
        try:
            if target == '.xlsx':
                import openpyxl
                book = openpyxl.load_workbook(path, read_only=True, data_only=True)
                try:
                    return bool(book.worksheets)
                finally:
                    book.close()
            import docx
            docx.Document(str(path))
            return True
        except Exception:                                          # noqa: BLE001
            return False


class _Office:
    """Word and Excel, started once and shared across every file.

    Starting Word per document costs several seconds each and leaves a process
    behind on any failure, so both are opened lazily, reused, and closed in a
    finally block by the caller.
    """

    def __init__(self, client, out, style):
        self.client = client
        self.out = out
        self.style = style
        self._word = None
        self._excel = None

    def _app(self, which):
        import pythoncom
        pythoncom.CoInitialize()
        app = self.client.DispatchEx(which)
        app.Visible = False
        # No dialogs. A prompt in an automated run is a hang, not a question:
        # there is nobody at the keyboard to answer it.
        app.DisplayAlerts = 0
        return app

    @property
    def word(self):
        if self._word is None:
            self._word = self._app('Word.Application')
        return self._word

    @property
    def excel(self):
        if self._excel is None:
            self._excel = self._app('Excel.Application')
        return self._excel

    def word_convert(self, source, target):
        # A nonsense password rather than none: a protected document then fails
        # immediately instead of stopping on a dialog nobody can see.
        doc = self.word.Documents.Open(
            str(source), ConfirmConversions=False, ReadOnly=True,
            AddToRecentFiles=False, PasswordDocument='no-password-here',
            Visible=False)
        try:
            doc.SaveAs2(str(target), FileFormat=WD_FORMAT_DOCX)
        finally:
            doc.Close(False)

    def excel_convert(self, source, target):
        book = self.excel.Workbooks.Open(
            str(source), UpdateLinks=0, ReadOnly=True,
            AddToMru=False, Password='no-password-here')
        try:
            book.SaveAs(str(target), FileFormat=XL_FORMAT_XLSX)
        finally:
            book.Close(False)

    def quit(self):
        for app in (self._word, self._excel):
            if app is None:
                continue
            try:
                app.Quit()
            except Exception:                                      # noqa: BLE001
                pass
