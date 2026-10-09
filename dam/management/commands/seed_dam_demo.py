# dam/management/commands/seed_dam_demo.py
"""Stand up two sample Spaces so the library can be judged before any real
content is migrated onto it.

Nothing is copied. Every sample Asset points at a file that already exists in
media/, so this adds database rows and thumbnails but not a single duplicated
byte — and because Django never deletes files on row deletion, `--undo` cannot
touch the originals.

    manage.py seed_dam_demo          # create
    manage.py seed_dam_demo --undo   # remove every sample row again
"""
import datetime
import os

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from dam import thumbnails
from dam.models import (Asset, AssetVersion, Audience, Colour, DAM_MANAGERS_GROUP,
                        Folder, Space, Tag)
from portal.models import BoothAlbum, BrandTemplateFile

BRAND_SLUG = 'brand-files'
BOOTH_SLUG = 'booth-archive'
DEMO_SLUGS = (BRAND_SLUG, BOOTH_SLUG)

#: Sample tags. Kept separate so --undo only removes tags it created and
#: leaves anything the user has since added alone.
DEMO_TAGS = ['logo', 'presentation', 'arabic', 'english', 'template',
             'event', 'photography']

PHOTOS_PER_ALBUM = 12
ALBUMS = 4


class Command(BaseCommand):
    help = 'Create (or remove) two sample library Spaces built from existing media.'

    def add_arguments(self, parser):
        parser.add_argument('--undo', action='store_true',
                            help='Delete the sample Spaces and their rows.')

    def handle(self, *args, **opts):
        if opts['undo']:
            return self.undo()
        with transaction.atomic():
            self.seed()

    # ── teardown ─────────────────────────────────────────────────────────
    def undo(self):
        thumbs = []
        for space in Space.objects.filter(slug__in=DEMO_SLUGS):
            for asset in Asset.objects.filter(folder__space=space):
                if asset.thumbnail:
                    try:
                        thumbs.append(asset.thumbnail.path)
                    except Exception:
                        pass
        n = Space.objects.filter(slug__in=DEMO_SLUGS).delete()
        # Generated thumbnails are ours, so they can go. Source files are not
        # touched — Django does not delete FileField files on row deletion, and
        # these rows never owned their file in the first place.
        for path in thumbs:
            try:
                os.remove(path)
            except OSError:
                pass
        removed = Tag.objects.filter(slug__in=DEMO_TAGS, assets__isnull=True).delete()
        self.stdout.write(self.style.SUCCESS(
            f'Removed sample spaces ({n[0]} rows), {len(thumbs)} thumbnails, '
            f'{removed[0]} unused tags. Source files untouched.'))

    # ── helpers ──────────────────────────────────────────────────────────
    def tag(self, name):
        slug = slugify(name)[:50] or 'tag'
        obj, _ = Tag.objects.get_or_create(slug=slug, defaults={'name': name.title()[:60]})
        return obj

    def folder(self, space, name, parent=None, **kw):
        f, _ = Folder.objects.get_or_create(space=space, parent=parent, name=name,
                                            defaults=kw)
        return f

    def asset(self, folder, name, path, owner, tags=(), description='', size_hint=None):
        """Register an existing media file as an Asset without copying it.

        Assigning the storage-relative path as a string leaves the FieldFile
        committed, so FileField.pre_save has nothing to write.
        """
        asset = Asset(folder=folder, name=name[:250], description=description[:500],
                      uploaded_by=owner, updated_by=owner)
        asset.file = path
        asset.save()
        if tags:
            asset.tags.set([self.tag(t) for t in tags])
        if asset.kind == 'image' and thumbnails.generate(asset):
            asset.save(update_fields=['thumbnail'])
        return asset

    # ── build ────────────────────────────────────────────────────────────
    def seed(self):
        if Space.objects.filter(slug__in=DEMO_SLUGS).exists():
            self.stdout.write(self.style.WARNING(
                'Sample spaces already exist. Run with --undo first to rebuild.'))
            return

        Group.objects.get_or_create(name=DAM_MANAGERS_GROUP)
        owner = (User.objects.filter(is_superuser=True).first()
                 or User.objects.filter(groups__name='Intranet Admin').first()
                 or User.objects.order_by('pk').first())

        n_assets = self.seed_brand(owner) + self.seed_booth(owner)
        self.stdout.write(self.style.SUCCESS(
            f'Created 2 spaces, {Folder.objects.filter(space__slug__in=DEMO_SLUGS).count()} '
            f'folders and {n_assets} assets — no files copied.'))

    def seed_brand(self, owner):
        space = Space.objects.create(
            slug=BRAND_SLUG, name='Brand Files', icon='image', order=10,
            description='Logos, presentation and document templates. '
                        'Always take the newest version from here.')

        review = datetime.date.today() + datetime.timedelta(days=180)

        logos = self.folder(space, 'Logos', icon='image', pinned=True,
                            colour=Colour.YELLOW, order=10, owner=owner,
                            description='Primary marks. The yellow logo is for '
                                        'dark backgrounds only.')
        png = self.folder(space, 'PNG — transparent', parent=logos, order=10,
                          description='Use for anything placed over colour or imagery.')
        jpg = self.folder(space, 'JPG — flat', parent=logos, order=20,
                          description='Use where transparency is not supported.')

        decks = self.folder(space, 'Presentation Templates', icon='grid', order=20,
                            colour=Colour.BLUE, owner=owner, review_date=review,
                            description='Client-facing and internal deck masters.')
        full = self.folder(space, 'Full Template', parent=decks, order=10)
        spec = self.folder(space, 'Specialised Template', parent=decks, order=20)
        internal = self.folder(
            space, 'Internal Template', parent=decks, order=30,
            colour=Colour.RED, audience=Audience.MANAGERS,
            description='Managers only — these decks carry internal figures.')

        docs = self.folder(space, 'Document Templates', icon='file-text', order=30,
                           colour=Colour.GREEN, owner=owner,
                           description='Reports, proposals and letterheads.')

        # Route each real brand file to a folder by the path it already sits in.
        routes = [
            ('/aji_logo/png/', png,      ['logo']),
            ('/aji_logo/jpg/', jpg,      ['logo']),
            ('/full_template/', full,     ['presentation', 'template']),
            ('/sepcialized_template/', spec, ['presentation', 'template']),
            ('/internal_template/', internal, ['presentation', 'template']),
        ]

        made = []
        for f in BrandTemplateFile.objects.exclude(file='').select_related('template'):
            path = f.file.name
            target, tags = docs, ['template']
            for needle, folder, folder_tags in routes:
                if needle in path:
                    target, tags = folder, list(folder_tags)
                    break
            label = (f.label or '').strip()
            variant = (f.version or '').strip()
            name = f'{label} — {variant}' if label and variant else (label or f.filename)
            if variant.lower() in ('arabic', 'english'):
                tags.append(variant.lower())
            made.append(self.asset(target, name, path, owner, tags,
                                   description=str(f.group) if f.group else ''))

        # Version history, so a replace can be judged before anyone risks one.
        # Points at the same file it supersedes, hence the explicit note.
        deck = next((a for a in made if a.kind == 'slide'), None)
        if deck:
            AssetVersion.objects.create(
                asset=deck, file=deck.file.name, version=1, size=deck.size,
                replaced_by=owner,
                note='Sample earlier version, so the history panel has something to show')
            deck.version = 2
            deck.save(update_fields=['version'])

        # One archived file, so the Archived filter is not empty.
        stale = next((a for a in made if a.folder_id == jpg.pk), None)
        if stale:
            stale.archived = True
            stale.save(update_fields=['archived'])

        return len(made)

    def seed_booth(self, owner):
        space = Space.objects.create(
            slug=BOOTH_SLUG, name='AJi Booth Archive', icon='video', order=20,
            description='Event photography, one folder per album.')

        albums = (BoothAlbum.objects.filter(photos__isnull=False)
                  .distinct().order_by('order', 'name'))
        n = 0
        for i, album in enumerate(albums[:ALBUMS], start=1):
            folder = self.folder(space, album.name, order=i * 10, icon='image',
                                 owner=owner,
                                 description=(album.period or '').strip())
            seq = 0
            for photo in album.photos.all()[:PHOTOS_PER_ALBUM]:
                if not photo.image:
                    continue
                seq += 1
                caption = (photo.caption or '').strip()
                # Sequential within the album, not the database id — "… 29" for
                # the first photo in a folder of five reads like a mistake.
                name = caption or f'{album.name} {seq:02d}'
                tags = ['event', 'photography']
                album_tag = str(album.tag or '').strip().lower()
                if album_tag and album_tag not in ('other', 'none', 'general'):
                    tags.append(album_tag[:60])
                self.asset(folder, name, photo.image.name, owner, tags,
                           description=caption)
                n += 1
        return n
