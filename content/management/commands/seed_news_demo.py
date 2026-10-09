"""Fill the news with sample articles and pictures, so the layout can be seen.

The homepage strip and the article gallery both only show what they are for once
there is more than a handful of stories and more than one picture on an article.
Five real articles and four attachments between them is not enough to look at.

Everything this makes is slugged `demo-`, which is how `--remove` finds exactly
its own work and nothing else. The pictures are generated here rather than
fetched: abstract compositions in the brand palette, each carrying a small
"sample" mark in the corner so nobody has to guess whether a photograph is real.
The body text says the same thing in the first line, because somebody who clicks
one of these should know inside a second that it is placeholder.

    python manage.py seed_news_demo
    python manage.py seed_news_demo --remove
"""
import os
import random
from datetime import date, timedelta
from io import BytesIO

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from content.models import NewsArticle, NewsAttachment, NewsCategory

#: Every slug this command creates starts with it. The whole removal story.
PREFIX = 'demo-'

#: The cover size the news editor crops to, so a generated one sits in the same
#: frame as a real photograph instead of being letterboxed against it.
COVER = (1600, 900)

PALETTE = {
    'yellow': (252, 215, 0),
    'ink':    (18, 18, 16),
    'paper':  (245, 244, 239),
    'shade':  (227, 226, 216),
    'sky':    (127, 196, 238),
    'clay':   (214, 138, 92),
    'violet': (163, 148, 224),
    'ok':     (95, 208, 142),
}

#: Title, category slug, and the shape drawn on its cover. Deliberately dull
#: subjects: a sample article that reads like a real announcement is a sample
#: article somebody forwards to a client.
STORIES = [
    ('Quarterly engineering review published',      'company',       'bands'),
    ('New site office opens in Aqaba',              'projects',      'grid'),
    ('Graduate engineer intake for the autumn',     'people',        'arcs'),
    ('Updated QHSE handbook now in the Hub',        'announcements', 'bars'),
    ('Water and Environment team wins tender',      'projects',      'circle'),
    ('AJi joins regional infrastructure forum',     'partnerships',  'contour'),
    ('Design standards refreshed for 2027',         'company',       'bands'),
    ('Long service awards announced',               'awards',        'arcs'),
    ('Digital survey tools rolled out on site',     'projects',      'grid'),
    ('Interiors studio expands its workshop',       'company',       'circle'),
]

#: Which of them get several pictures, so the gallery has something to page
#: through, and roughly how many.
GALLERIES = {0: 4, 4: 3, 8: 5}


class Command(BaseCommand):
    help = 'Create (or remove) sample news articles with generated pictures.'

    def add_arguments(self, parser):
        parser.add_argument('--remove', action='store_true',
                            help='Delete everything this command created.')
        parser.add_argument('--count', type=int, default=len(STORIES),
                            help='How many articles to make.')

    def handle(self, *args, **options):
        if options['remove']:
            return self._remove()

        try:
            from PIL import Image                                  # noqa: F401
        except ImportError:
            self.stderr.write('Pillow is needed to draw the pictures.')
            return

        cats = {c.slug: c for c in NewsCategory.objects.all()}
        today = date.today()
        made = pictures = 0

        for i, (title, cat_slug, shape) in enumerate(STORIES[:options['count']]):
            slug = PREFIX + slugify(title)[:40]
            if NewsArticle.objects.filter(slug=slug).exists():
                self.stdout.write(f'  already there: {title}')
                continue

            article = NewsArticle.objects.create(
                title=title,
                slug=slug,
                category=cats.get(cat_slug),
                author_name='AJi Hub',
                excerpt=('Sample article, added to show how the news pages lay '
                         'out. It carries no real announcement.'),
                body=self._body(title),
                # Spread back from yesterday, so the real article dated today
                # keeps the lead position on the homepage.
                date=today - timedelta(days=i + 1),
                published=True,
            )
            article.image.save(f'{slug}-cover.png',
                               self._cover(shape, i), save=True)
            made += 1

            for n in range(GALLERIES.get(i, 0)):
                NewsAttachment.objects.create(
                    article=article,
                    file=ContentFile(self._plate(i, n).read(),
                                     name=f'{slug}-{n + 1}.png'),
                    caption=f'Sample picture {n + 1} of {GALLERIES[i]}')
                pictures += 1

            # One document as well, so the gallery and the download list are
            # both on screen together somewhere.
            if i == 0:
                NewsAttachment.objects.create(
                    article=article,
                    file=ContentFile(self._pdf(), name=f'{slug}-notes.pdf'),
                    caption='Sample attachment (not a real document)')

            self.stdout.write(f'  {article.date}  {title}'
                              + (f'   +{GALLERIES[i]} pictures' if i in GALLERIES else ''))

        self.stdout.write(self.style.SUCCESS(
            f'{made} sample article(s) and {pictures} picture(s) added.'))
        self.stdout.write('Remove them all again with: '
                          'manage.py seed_news_demo --remove')

    # ── removal ─────────────────────────────────────────────────────────────

    def _remove(self):
        qs = NewsArticle.objects.filter(slug__startswith=PREFIX)
        n = qs.count()
        if not n:
            self.stdout.write('Nothing to remove.')
            return
        files = 0
        for article in qs:
            for a in article.attachments.all():
                files += self._unlink(a.file)
            files += self._unlink(article.image)
        qs.delete()
        self.stdout.write(self.style.SUCCESS(
            f'{n} sample article(s) removed, with {files} file(s).'))

    def _unlink(self, field):
        if not field:
            return 0
        try:
            path = field.path
        except (ValueError, NotImplementedError):
            return 0
        try:
            if os.path.isfile(path):
                os.remove(path)
                return 1
        except OSError:
            pass
        return 0

    # ── the words ───────────────────────────────────────────────────────────

    def _body(self, title):
        return (
            'This is sample content added to the Hub so the news layout can be '
            'reviewed. It is not a real announcement and can be deleted at any '
            'time.\n\n'
            f'{title} — placeholder paragraph. Consultants, engineers and site '
            'teams across the group would normally find the detail here, with '
            'the dates, the names and the next steps set out in full.\n\n'
            'A second paragraph, so the article is long enough to show how the '
            'body, the pictures and the comments sit together on the page.')

    # ── the pictures ────────────────────────────────────────────────────────

    def _cover(self, shape, seed):
        """An abstract cover at the size the editor crops covers to."""
        from PIL import Image, ImageDraw

        rng = random.Random(seed * 977 + 13)
        w, h = COVER
        ground = PALETTE['ink'] if seed % 3 == 0 else PALETTE['paper']
        img = Image.new('RGB', (w, h), ground)
        d = ImageDraw.Draw(img)
        accent = PALETTE[rng.choice(['yellow', 'sky', 'clay', 'violet', 'ok'])]
        line = PALETTE['paper'] if ground == PALETTE['ink'] else PALETTE['ink']

        if shape == 'bands':
            for i in range(9):
                x = -h + i * 210
                d.polygon([(x, h), (x + 120, h), (x + 120 + h, 0), (x + h, 0)],
                          fill=accent if i % 3 == 0 else PALETTE['shade']
                          if ground == PALETTE['paper'] else (34, 34, 31))
        elif shape == 'grid':
            cell, pad = 118, 90
            for row in range(6):
                for col in range(11):
                    x, y = pad + col * cell, pad + row * cell
                    hot = (row * 11 + col) % 17 == 3
                    d.rectangle([x, y, x + cell - 16, y + cell - 16],
                                fill=accent if hot else None,
                                outline=None if hot else line, width=2)
        elif shape == 'arcs':
            for i in range(7):
                r = 160 + i * 130
                d.arc([w // 2 - r, h - r, w // 2 + r, h + r], 180, 360,
                      fill=accent if i % 2 == 0 else line, width=14)
        elif shape == 'bars':
            base, bw = h - 110, 96
            for i in range(11):
                bh = 90 + rng.randrange(0, 520)
                x = 120 + i * (bw + 30)
                d.rectangle([x, base - bh, x + bw, base],
                            fill=accent if i % 4 == 1 else line)
        elif shape == 'circle':
            d.ellipse([w // 2 - 300, h // 2 - 300, w // 2 + 300, h // 2 + 300],
                      fill=accent)
            d.line([(0, h // 2 + 190), (w, h // 2 + 190)], fill=line, width=8)
        else:                                                     # contour
            for i in range(16):
                off = i * 46
                d.line([(0, 240 + off // 2), (w // 3, 150 + off),
                        (2 * w // 3, 320 + off // 3), (w, 120 + off)],
                       fill=accent if i % 4 == 0 else line, width=5, joint='curve')

        self._mark(img, 'AJi Hub · sample image', line)
        return self._png(img)

    def _plate(self, seed, n):
        """One picture for a gallery. Sizes vary so the viewer has to cope."""
        from PIL import Image, ImageDraw

        rng = random.Random(seed * 31 + n)
        size = [(1400, 900), (900, 1200), (1100, 1100)][n % 3]
        ground = PALETTE['paper'] if n % 2 else PALETTE['shade']
        img = Image.new('RGB', size, ground)
        d = ImageDraw.Draw(img)
        accent = PALETTE[rng.choice(['yellow', 'sky', 'clay', 'violet', 'ok'])]
        w, h = size
        for i in range(rng.randrange(5, 10)):
            x, y = rng.randrange(0, w - 200), rng.randrange(0, h - 200)
            s = rng.randrange(120, 380)
            if i % 2:
                d.ellipse([x, y, x + s, y + s], outline=PALETTE['ink'], width=6)
            else:
                d.rectangle([x, y, x + s, y + s // 2], fill=accent)
        d.rectangle([0, 0, w - 1, h - 1], outline=PALETTE['ink'], width=6)
        self._mark(img, f'sample picture {n + 1}', PALETTE['ink'])
        return self._png(img)

    def _mark(self, img, text, colour):
        """A small corner caption, so a generated picture says that it is one."""
        from PIL import ImageDraw, ImageFont

        d = ImageDraw.Draw(img)
        font = None
        for name in ('DraftB-Bold.ttf', 'DraftB-Regular.ttf'):
            path = os.path.join(settings.BASE_DIR, 'static', 'fonts', name)
            if os.path.isfile(path):
                try:
                    font = ImageFont.truetype(path, 26)
                    break
                except OSError:
                    pass
        if font is None:
            font = ImageFont.load_default()
        pad = 26
        d.text((pad, img.height - pad - 30), text, fill=colour, font=font)

    def _png(self, img):
        buf = BytesIO()
        img.save(buf, format='PNG', optimize=True)
        buf.seek(0)
        return ContentFile(buf.read())

    def _pdf(self):
        """A one-page PDF, written by hand so nothing has to be installed."""
        body = (b'BT /F1 22 Tf 72 720 Td (Sample attachment) Tj '
                b'0 -34 Td /F1 12 Tf (Placeholder document added by '
                b'seed_news_demo.) Tj ET')
        objs = [
            b'<< /Type /Catalog /Pages 2 0 R >>',
            b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] '
            b'/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',
            b'<< /Length ' + str(len(body)).encode() + b' >>\nstream\n'
            + body + b'\nendstream',
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        ]
        out = bytearray(b'%PDF-1.4\n')
        offsets = []
        for i, o in enumerate(objs, start=1):
            offsets.append(len(out))
            out += f'{i} 0 obj\n'.encode() + o + b'\nendobj\n'
        start = len(out)
        out += f'xref\n0 {len(objs) + 1}\n'.encode()
        out += b'0000000000 65535 f \n'
        for off in offsets:
            out += f'{off:010d} 00000 n \n'.encode()
        out += (f'trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n'
                f'startxref\n{start}\n%%EOF\n').encode()
        return bytes(out)
