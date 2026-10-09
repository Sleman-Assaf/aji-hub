"""Remove documents that belong to no library.

`Policy.category` is `on_delete=SET_NULL`, so deleting a document library never
deleted the documents in it — it cut them loose. They stayed in the database with
no library, and the only place they surfaced was an "Other Documents" tile on
Policies & Manuals that linked to `?cat=0`, which matches no library and so
opened nothing. Two deleted libraries left 99 documents there.

Both delete paths now take their documents with them
(`portal.libraries.library_remove` and `portal.views.policy_category_delete`), so
nothing new arrives here. This is for the ones already stranded.

It reports before it acts, and `--dry-run` does only the reporting. A document
whose title exists in a real library is called out as such, because that is the
difference between deleting a duplicate and deleting the only copy.
"""
import os

from django.core.management.base import BaseCommand
from django.db import transaction

from portal.models import Policy


class Command(BaseCommand):
    help = 'Delete documents left with no library by a deleted one.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would go and change nothing.')
        parser.add_argument('--keep-unique', action='store_true',
                            help='Delete only the ones whose title exists in a '
                                 'real library, i.e. the duplicates.')

    def handle(self, *args, **options):
        dry = options['dry_run']
        orphans = list(Policy.objects.filter(category__isnull=True))
        if not orphans:
            self.stdout.write('Nothing to do — no document is without a library.')
            return

        # Where else the same title lives. Compared on the title because that is
        # what a person reads; two rows for one document is exactly what a
        # re-import leaves behind.
        filed = {}
        for p in (Policy.objects.filter(category__isnull=False)
                  .select_related('category', 'folder')):
            filed.setdefault(p.title.strip().lower(), []).append(p)

        rows = []
        for p in orphans:
            twin = filed.get(p.title.strip().lower())
            on_disk = False
            if p.file:
                try:
                    on_disk = os.path.isfile(p.file.path)
                except Exception:
                    on_disk = False
            rows.append((p, twin[0] if twin else None, on_disk))

        dupes = [r for r in rows if r[1] is not None]
        unique = [r for r in rows if r[1] is None]

        self.stdout.write(self.style.MIGRATE_HEADING(
            f'{len(rows)} document(s) belong to no library'))
        self.stdout.write(f'  {len(dupes)} also exist in a library')
        self.stdout.write(f'  {len(unique)} exist nowhere else')
        self.stdout.write(f'  {sum(1 for r in rows if r[2])} still have their file on disk')

        for p, twin, on_disk in unique:
            self.stdout.write(self.style.WARNING(
                f'  only copy: "{p.title}"'
                f'{" — file already gone" if not on_disk else ""}'))

        going = dupes if options['keep_unique'] else rows
        if options['keep_unique'] and unique:
            self.stdout.write(f'Keeping the {len(unique)} that exist nowhere else.')

        if dry:
            self.stdout.write(self.style.NOTICE(
                f'Dry run — {len(going)} would be deleted. Nothing changed.'))
            return

        files_gone = 0
        with transaction.atomic():
            for p, _twin, on_disk in going:
                if on_disk:
                    try:
                        os.remove(p.file.path)
                        files_gone += 1
                    except OSError as exc:
                        # The record still goes: a file that cannot be removed is
                        # a stray file, and keeping the row to match it only
                        # hides the row as well.
                        self.stdout.write(self.style.WARNING(
                            f'  could not delete {p.file.name}: {exc}'))
            Policy.objects.filter(pk__in=[p.pk for p, _t, _d in going]).delete()

        self.stdout.write(self.style.SUCCESS(
            f'Deleted {len(going)} document record(s) and {files_gone} file(s).'))
        left = Policy.objects.filter(category__isnull=True).count()
        self.stdout.write(f'{left} document(s) now belong to no library.')
