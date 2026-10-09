# portal/management/commands/rebuild_booth_albums.py
"""Rebuilds the AJi Booth albums from the files already on disk.

Scans MEDIA_ROOT/brand/booth/<album_folder>/ — each folder becomes an album
(named from the folder name), and every file in its photos/ and videos/
subfolders is registered. Existing DB rows are kept; already-registered files
are skipped, so the command is safe to re-run.

Usage:
    python manage.py rebuild_booth_albums
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from portal.models import BoothAlbum, BoothPhoto, BoothVideo

PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
VIDEO_EXTENSIONS = {'.' + e for e in BoothVideo.ALLOWED_EXTENSIONS}

# keyword → tag guess, based on the folder name
TAG_KEYWORDS = [
    (('football', 'fifa', 'league', 'sport', 'match', 'tournament'), 'sport'),
    (('ergonomic', 'wellness', 'yoga', 'health', 'fitness'), 'wellness'),
    (('eid', 'celebration', 'party', 'anniversary', 'iftar'), 'celebration'),
    (('award', 'recognition', 'honor', 'honour'), 'recognition'),
    (('career', 'earth', 'day', 'fair', 'company', 'townhall'), 'company-wide'),
]

# words that need special casing in album names
SPECIAL_WORDS = {'aji': 'AJi', 'fifa': 'FIFA', 'hq': 'HQ', 'it': 'IT'}


def folder_to_name(folder):
    words = folder.replace('-', '_').split('_')
    return ' '.join(SPECIAL_WORDS.get(w.lower(), w.capitalize()) for w in words if w)


def guess_tag(folder):
    low = folder.lower()
    for keywords, tag in TAG_KEYWORDS:
        if any(k in low for k in keywords):
            return tag
    return 'other'


class Command(BaseCommand):
    help = 'Rebuilds Booth albums (photos & videos) from the files under media/brand/booth/.'

    def handle(self, *args, **options):
        booth_root = Path(settings.MEDIA_ROOT) / 'brand' / 'booth'
        if not booth_root.is_dir():
            self.stdout.write(self.style.ERROR(f'Folder not found: {booth_root}'))
            return

        albums = photos_added = videos_added = 0

        for album_dir in sorted(p for p in booth_root.iterdir() if p.is_dir()):
            folder = album_dir.name
            name = folder_to_name(folder)
            album, created = BoothAlbum.objects.get_or_create(
                name=name, defaults={'tag': guess_tag(folder)},
            )
            albums += 1
            label = 'created' if created else 'exists'

            existing_photos = set(album.photos.values_list('image', flat=True))
            existing_videos = set(album.videos.values_list('video', flat=True))
            first_photo_rel = None

            # ── photos/ ──
            photos_dir = album_dir / 'photos'
            if photos_dir.is_dir():
                for f in sorted(photos_dir.iterdir()):
                    if not f.is_file() or f.suffix.lower() not in PHOTO_EXTENSIONS:
                        continue
                    rel = f'brand/booth/{folder}/photos/{f.name}'
                    if first_photo_rel is None:
                        first_photo_rel = rel
                    if rel in existing_photos:
                        continue
                    photo = BoothPhoto(album=album)
                    photo.image.name = rel  # file already on disk — register, don't copy
                    photo.save()
                    photos_added += 1

            # ── videos/ ──
            videos_dir = album_dir / 'videos'
            if videos_dir.is_dir():
                for f in sorted(videos_dir.iterdir()):
                    if not f.is_file() or f.suffix.lower() not in VIDEO_EXTENSIONS:
                        continue
                    rel = f'brand/booth/{folder}/videos/{f.name}'
                    if rel in existing_videos:
                        continue
                    video = BoothVideo(album=album)
                    video.video.name = rel
                    video.save()
                    videos_added += 1

            # ── cover: image directly in the album folder, else first photo ──
            if not album.cover_photo:
                cover_rel = None
                for f in sorted(album_dir.iterdir()):
                    if f.is_file() and f.suffix.lower() in PHOTO_EXTENSIONS:
                        cover_rel = f'brand/booth/{folder}/{f.name}'
                        break
                cover_rel = cover_rel or first_photo_rel
                if cover_rel:
                    album.cover_photo.name = cover_rel

            album.photo_count = album.photos.count()
            album.save()

            self.stdout.write(
                f'  {album.name} ({label}) — {album.photos.count()} photos, '
                f'{album.videos.count()} videos, tag={album.tag}'
            )

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. {albums} albums processed, {photos_added} photos and '
            f'{videos_added} videos registered.'
        ))
