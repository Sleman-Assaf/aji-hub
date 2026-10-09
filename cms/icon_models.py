# cms/icon_models.py
"""User-managed icons.

Every icon on the Hub is a row here, so the whole set — including navigation and
button icons — can be changed, replaced or added to without touching code.

Two deliberate safety properties, because navigation and buttons now depend on
this table:

* A missing or blanked row falls back to the shape compiled into cms/icons.py.
  Emptying an icon can make it plain again; it can never leave a blank button or
  an invisible nav item.
* Uploaded SVG is sanitised to shape elements only. Icons render inline inside
  every page, so a <script> in an icon file would be a site-wide XSS.
"""
import re

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import models
from django.utils.text import slugify

from . import phosphor
from .icons import ICONS as CONTENT_ICONS
from .icons_ui import FILLED_ICONS, NAV_ICONS, UI_ICONS

#: The 76 hand-drawn shapes the Hub shipped with. They are no longer the source of
#: an icon — Phosphor is — but they remain the last fallback, so a name that the
#: icon set has never heard of still draws something rather than nothing.
BUILTIN_ICONS = {**CONTENT_ICONS, **NAV_ICONS, **UI_ICONS}

#: Bumped when the resolution order changes, so a cached map from the previous
#: arrangement is not read back after a deploy.
CACHE_KEY = 'cms_icon_map_v6'

#: Icons that only ever appear in markup built by JavaScript — the chat's own
#: controls, the row menus, the reaction bar. They are not in any content field,
#: so names_in_use() cannot find them, and they are not on the picker's
#: shortlist, so COMMON does not carry them either. Without this list they
#: resolve on the server and silently draw nothing in the browser, which is the
#: worst of the three possible outcomes.
JS_ICONS = [
    'push-pin', 'dots-three', 'dots-three-vertical', 'pencil-simple', 'file',
    'x', 'smiley', 'arrow-bend-up-left', 'arrows-out-simple', 'caret-left',
    'trash', 'check', 'plus', 'users-three', 'download-simple',
    # The call layer draws its own controls and its roster — the answer and
    # decline buttons on a ringing call exist only in JavaScript, so without
    # these the phone would ring with two blank buttons on it.
    'phone-call', 'phone-x', 'microphone', 'microphone-slash',
    'monitor-arrow-up',
    # And the call log's rows, which say which way each call went. Neither of
    # these is on the picker's shortlist, so without them every line in the log
    # would be missing the one mark that distinguishes it from the line above.
    'phone-incoming', 'phone-outgoing',
    # The notice that replaces the reply banner when an edit runs out of time,
    # and the badge on a group's picture that changes it.
    'info', 'camera',
    # The navigation's own menu — drawn in a template, but listed here so the
    # icon library keeps them and the picker cannot lose one by pruning.
    'sign-out', 'chevron-down', 'briefcase-2', 'user', 'settings',
]

#: Where an icon name can appear in content. The serialised map handed to the
#: browser covers these, so an icon chosen anywhere on the Hub also renders in the
#: parts of the interface that build their own markup — the navigation, the chat
#: panel, toasts. Without it, picking one of the 1,512 new icons for a nav item
#: would draw nothing there while working everywhere else.
ICON_FIELDS = [
    ('cms', 'Section', 'icon'),
    ('cms', 'SectionItem', 'icon'),
    ('cms', 'SectionItem', 'button_icon'),
    ('portal', 'PolicyCategory', 'icon'),
    ('portal', 'WellnessProgram', 'icon'),
    ('portal', 'ExploreTab', 'icon'),
    ('portal', 'GrowthCertification', 'icon'),
    ('services', 'ServiceCategory', 'icon'),
    ('services', 'Service', 'icon'),
    ('exchange', 'Community', 'icon'),
    ('dam', 'Space', 'icon'),
    ('dam', 'Folder', 'icon'),
]

#: The grid the built-in icons are drawn on.
DEFAULT_VIEW_BOX = '0 0 24 24'

#: Elements an icon may contain. Everything else is dropped.
ALLOWED_TAGS = {'path', 'circle', 'rect', 'line', 'polyline', 'polygon',
                'ellipse', 'g', 'defs', 'use', 'title'}

#: Attributes kept on those elements.
ALLOWED_ATTRS = {
    'd', 'cx', 'cy', 'r', 'rx', 'ry', 'x', 'y', 'x1', 'y1', 'x2', 'y2',
    'width', 'height', 'points', 'transform', 'fill', 'stroke',
    'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'opacity',
    'fill-rule', 'clip-rule', 'id', 'href',
}

_TAG_RE = re.compile(r'<\s*(/?)\s*([a-zA-Z0-9:-]+)([^>]*?)(/?)\s*>', re.S)
_ATTR_RE = re.compile(r'([a-zA-Z-]+)\s*=\s*("[^"]*"|\'[^\']*\')')
_SVG_WRAPPER_RE = re.compile(r'</?svg[^>]*>', re.I)
_VIEWBOX_RE = re.compile(r'<svg[^>]*\bviewBox\s*=\s*["\']([^"\']+)["\']', re.I)
_WH_RE = re.compile(r'<svg[^>]*\bwidth\s*=\s*["\']([\d.]+)[a-z%]*["\'][^>]*'
                    r'\bheight\s*=\s*["\']([\d.]+)[a-z%]*["\']', re.I)

#: Attribute values that mean "a specific colour" rather than "inherit". They are
#: dropped so an icon takes the colour of the text around it — an uploaded icon
#: with fill="#333" ignored the theme entirely.
_KEEP_PAINT = {'none', 'currentcolor', 'transparent', 'inherit'}


def extract_view_box(markup):
    """The coordinate system an uploaded SVG was drawn in.

    Icons are authored on all sorts of grids — 16, 20, 24, 48, 512. Forcing every
    one into a 24×24 box is what made an uploaded icon invisible: a shape drawn at
    x=256 sits far outside a 24-unit viewport, so the visible corner is empty.
    """
    if not markup:
        return ''
    m = _VIEWBOX_RE.search(markup)
    if m:
        parts = m.group(1).replace(',', ' ').split()
        if len(parts) == 4:
            try:
                [float(p) for p in parts]
                return ' '.join(parts)
            except ValueError:
                pass
    # No viewBox, but width and height still describe the grid.
    m = _WH_RE.search(markup)
    if m:
        return f'0 0 {m.group(1)} {m.group(2)}'
    return ''


def looks_filled(markup):
    """True when the shapes are painted rather than outlined.

    A solid icon rendered with a stroke and no fill comes out as a hollow
    silhouette, so this is detected rather than left to the uploader to notice.
    """
    if not markup:
        return False
    fills = re.findall(r'fill\s*=\s*["\']([^"\']+)["\']', markup, re.I)
    real_fill = [f for f in fills if f.strip().lower() not in _KEEP_PAINT]
    has_stroke = bool(re.search(r'stroke\s*=\s*["\'](?!none)', markup, re.I))
    return bool(real_fill) and not has_stroke


def sanitise_svg(markup):
    """Strip an SVG down to inner shape markup that is safe to inline.

    Accepts either a full <svg> document or just its inner shapes, and returns
    the inner shapes. Anything with script, event handlers, foreign objects or
    external references is discarded rather than escaped, because the result is
    rendered unescaped by design.
    """
    if not markup:
        return ''
    body = _SVG_WRAPPER_RE.sub('', markup)

    out = []
    for match in _TAG_RE.finditer(body):
        closing, tag, attrs, self_close = match.groups()
        tag_l = tag.lower()
        if tag_l not in ALLOWED_TAGS:
            continue
        if closing:
            out.append(f'</{tag_l}>')
            continue
        kept = []
        for name, raw in _ATTR_RE.findall(attrs or ''):
            name_l = name.lower()
            if name_l not in ALLOWED_ATTRS:
                continue
            value = raw[1:-1].strip()
            low = value.lower()
            # url(), javascript: and external hrefs are all routes to loading
            # something the author of the icon did not intend.
            if 'javascript:' in low or 'url(' in low or '<' in value:
                continue
            if name_l == 'href' and not value.startswith('#'):
                continue
            # A hardcoded colour is dropped so the icon inherits the surrounding
            # text colour, which is how every other icon on the Hub behaves.
            if name_l in ('fill', 'stroke') and low not in _KEEP_PAINT:
                continue
            kept.append(f'{name_l}="{value}"')
        attr_str = (' ' + ' '.join(kept)) if kept else ''
        out.append(f'<{tag_l}{attr_str}{"/" if self_close else ""}>')
    return ''.join(out)


class IconCategory(models.TextChoices):
    GENERAL   = 'general',   'General'
    NAV       = 'nav',       'Navigation'
    UI        = 'ui',        'Interface and buttons'
    CONTENT   = 'content',   'Content'
    CUSTOM    = 'custom',    'Uploaded'


class Icon(models.Model):
    """One named icon. `body` is inner SVG for a 24x24 stroked viewBox."""
    name  = models.SlugField(max_length=60, unique=True,
                             help_text='The key templates and content refer to, e.g. "calendar".')
    label = models.CharField(max_length=80, blank=True,
                             help_text='Shown in the picker. Defaults to the name.')
    body  = models.TextField(
        blank=True,
        help_text='Inner SVG for a 24×24 viewBox. Paste a whole <svg> and the '
                  'wrapper is stripped. Leave empty to use the built-in shape.')
    category = models.CharField(max_length=12, choices=IconCategory.choices,
                                default=IconCategory.GENERAL, db_index=True)

    #: Set on the icons the Hub ships with. They can be edited and reset but not
    #: deleted, because templates reference them by name.
    is_builtin = models.BooleanField(default=False, editable=False)

    #: A raster or image icon — PNG, JPG, WEBP, or an SVG kept as a file rather
    #: than inlined. An icon does not have to be line art: a logo, a partner mark
    #: or a photo badge is a legitimate icon, and refusing anything but SVG meant
    #: those could not be used at all.
    #:
    #: It is drawn inside the same box as a drawn icon and scaled to fit, so an
    #: uploaded image and a built-in shape are interchangeable everywhere.
    image = models.ImageField(upload_to='cms/icons/', blank=True, null=True)

    #: The grid the shapes were drawn on. Uploaded icons come on all sorts of
    #: grids, and forcing them into 24×24 put the artwork outside the viewport.
    view_box = models.CharField(
        max_length=60, blank=True, default='',
        help_text='Coordinate system, e.g. "0 0 24 24". Read from the uploaded '
                  'SVG automatically; leave empty for 24×24.')

    #: Stroke width suits most Lucide shapes; a few uploads need a different one.
    stroke = models.DecimalField(max_digits=3, decimal_places=1, default=2,
                                 help_text='Line thickness. 1.6–2 suits most icons.')
    filled = models.BooleanField(
        default=False,
        help_text='Tick for solid icons — fills the shape instead of stroking it.')

    order = models.IntegerField(default=0)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True,
                                    blank=True, related_name='icons')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'order', 'name']

    def __str__(self):
        return self.label or self.name

    def save(self, *args, **kwargs):
        self.name = slugify(self.name)[:60]
        if not self.label:
            self.label = self.name.replace('-', ' ').title()
        # The grid and the paint style are read from the raw upload, before the
        # wrapper is stripped — the <svg> tag is the only place they live.
        raw = self.body or ''
        if raw.strip():
            found = extract_view_box(raw)
            if found and found != '0 0 24 24':
                self.view_box = found
            if looks_filled(raw):
                self.filled = True
        self.body = sanitise_svg(raw)
        super().save(*args, **kwargs)
        # Both copies. Clearing only the map left the browser drawing from JSON
        # up to a day old, so an edited icon changed on the page and not in the
        # chat, the picker or the navigation builder.
        forget_icon_map()

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        forget_icon_map()

    @property
    def effective_body(self):
        """This icon's shape, or the shipped one if it has been left empty.

        A picture icon has no shape at all: falling back to a drawn shape here
        would put a line icon underneath an uploaded logo.
        """
        if self.image:
            return ''
        return self.body or shipped_shape(self.name)[0]

    @property
    def is_overridden(self):
        return bool(self.body) and bool(shipped_shape(self.name)[0])

    def reset(self):
        """Drop a customisation and go back to the shipped shape."""
        self.body = ''
        self.save()


def shipped_shape(name):
    """The shape the Hub ships for `name`: (body, stroke, filled, view_box).

    Phosphor first, the old hand-drawn set second. Both are "shipped" as far as an
    editor is concerned — the difference matters only to the renderer, because
    Phosphor's regular weight is a filled path on a 256 grid while the old shapes
    are stroked outlines on a 24 grid.

    Returns an empty body when neither knows the name.
    """
    body = phosphor.path(name)
    if body:
        return body, 2.0, True, phosphor.VIEW_BOX
    body = BUILTIN_ICONS.get(name, '')
    return body, 2.0, name in FILLED_ICONS, DEFAULT_VIEW_BOX


def names_in_use():
    """Every icon name content actually refers to.

    The whole set is 1,512 icons; serialising all of it into every page for the
    JavaScript-drawn parts of the interface would add most of a megabyte to each
    request. This is the bounded alternative: the names in use, which is what those
    parts can actually be asked to draw.
    """
    from django.apps import apps
    found = set()
    for app_label, model_name, field in ICON_FIELDS:
        try:
            model = apps.get_model(app_label, model_name)
            manager = getattr(model, 'all_objects', model._default_manager)
            found.update(manager.exclude(**{field: ''})
                         .values_list(field, flat=True).distinct())
        except Exception:
            # A missing app or an unmigrated table must not stop the icons that do
            # resolve from rendering.
            continue
    return {n for n in found if n}


def icon_map():
    """{name: (body, stroke, filled, view_box, image_url)} for the names in play.

    "In play" is the old hand-drawn names, the shortlist the picker opens on, every
    name content refers to, and every row in the library. Not all 1,512 — see
    names_in_use.

    Cached because icons render dozens of times per page. Every write to Icon
    clears it.
    """
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    wanted = (set(BUILTIN_ICONS) | set(phosphor.COMMON) | set(phosphor.LEGACY)
              | set(JS_ICONS))
    wanted |= names_in_use()

    data = {}
    for name in wanted:
        body, stroke, filled, box = shipped_shape(name)
        if body:
            data[name] = (body, stroke, filled, box, '')

    try:
        # A library row wins over the shipped shape: that is what "editable" means.
        for icon in Icon.objects.all():
            if icon.image:
                try:
                    data[icon.name] = ('', 2.0, False, DEFAULT_VIEW_BOX, icon.image.url)
                    continue
                except Exception:
                    pass
            if icon.body:
                # Only a row with its own shape overrides. A blank one is not an
                # instruction to draw nothing; it means "use what ships", which is
                # already in the dict above.
                box = icon.view_box or DEFAULT_VIEW_BOX
                data[icon.name] = (icon.body, float(icon.stroke), icon.filled, box, '')
            elif icon.name not in data:
                body, stroke, filled, box = shipped_shape(icon.name)
                if body:
                    data[icon.name] = (body, stroke, filled, box, '')
    except Exception:
        # Before migrations run, or if the table is unavailable, the shipped
        # shapes still render. Chrome must not depend on a query succeeding.
        pass

    cache.set(CACHE_KEY, data, 3600)
    return data


def render_icon(name, size=18, stroke=None, classes=''):
    """Inline SVG for a named icon. Unknown names render nothing."""
    if not name:
        return ''
    entry = icon_map().get(name)
    if not entry:
        # A name outside the cached map — an icon chosen since it was built, or one
        # a template asks for directly. Resolving it here rather than returning
        # nothing is why picking any of the 1,512 works immediately instead of
        # after the cache expires.
        body, stroke_w, filled, box = shipped_shape(name)
        if not body:
            return ''
        entry = (body, stroke_w, filled, box, '')
    body, default_stroke, filled, box, image = entry

    # An uploaded picture is drawn in the same box as a drawn icon and scaled to
    # fit, so the two are interchangeable: same footprint, aspect ratio kept,
    # never stretched or cropped.
    if image:
        cls = f' {classes}' if classes else ''
        return (f'<img class="ic-img{cls}" src="{image}" alt="" width="{size}" '
                f'height="{size}" loading="lazy" decoding="async">')

    if not body:
        return ''
    sw = stroke if stroke is not None else default_stroke
    # A thin stroke drawn on a large grid disappears, and a thick one on a small
    # grid turns to mud, so the width is scaled to the coordinate system.
    try:
        span = float(box.split()[2])
        if span and span != 24:
            sw = round(float(sw) * span / 24, 2)
    except (ValueError, IndexError):
        pass
    fill = 'currentColor' if filled else 'none'
    stroke_attr = '' if filled else f' stroke="currentColor" stroke-width="{sw}"'
    cls = f' class="{classes}"' if classes else ''
    return (f'<svg{cls} width="{size}" height="{size}" viewBox="{box}" '
            f'fill="{fill}"{stroke_attr} stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{body}</svg>')


#: Cache key for the serialised icon set and its stamp.
PAYLOAD_KEY = 'cms_icon_payload_v1'


def forget_icon_map():
    """Throw away both cached copies of the icon set.

    Two keys, and both have to go together. The map is what the server draws
    from; the payload is the JSON the browser draws from. Clearing only the first
    left the browser working from a set up to a day old.
    """
    cache.delete(CACHE_KEY)
    cache.delete(PAYLOAD_KEY)


def watch_icon_fields():
    """Rebuild the icon set when anything chooses a new icon.

    The set sent to the browser holds only the icons actually in use, worked out
    by scanning the fields in ICON_FIELDS. That result was cached and only ever
    thrown away when an `Icon` row was edited — so choosing an icon for a
    community, a policy category or a brand tile changed what was in use and
    nothing noticed.

    The effect was oddly specific and hard to place: the icon appeared on the
    page, because the server resolves those from the database directly, but
    anywhere the interface builds its own markup — the picker's own preview, the
    chat, the navigation builder — asked the browser's copy, which had never
    heard of it and drew nothing. So you would choose an icon, see it on the
    card, and find the field you chose it in still showing empty.

    Connected once at startup from cms/apps.py.
    """
    from django.apps import apps as django_apps
    from django.db.models.signals import post_delete, post_save

    def clear(sender, **kwargs):
        forget_icon_map()

    seen = set()
    for app_label, model_name, _field in ICON_FIELDS:
        key = (app_label, model_name)
        if key in seen:
            continue
        seen.add(key)
        try:
            model = django_apps.get_model(app_label, model_name)
        except LookupError:
            # A model listed here but not installed is not an error: ICON_FIELDS
            # is a map of where icons can live, and apps come and go.
            continue
        uid = f'icons_in_use_{app_label}_{model_name}'
        post_save.connect(clear, sender=model, dispatch_uid=uid, weak=False)
        post_delete.connect(clear, sender=model, dispatch_uid=uid, weak=False)


def icon_payload():
    """(json, stamp) for the icon set the browser uses.

    58 KB of icon data was being written into every page of the Hub, identical
    every time and impossible to cache, which made it the single largest thing
    the site sent. It is served from its own URL now, and the stamp — a short
    hash of the content — is what lets that URL be cached forever and still
    change the moment somebody edits an icon.
    """
    import hashlib
    import json

    cached = cache.get(PAYLOAD_KEY)
    if cached is not None:
        return cached

    data = {name: {'d': body, 'w': stroke, 'f': filled, 'v': box, 'img': image}
            for name, (body, stroke, filled, box, image) in icon_map().items()
            if body or image}
    payload = json.dumps(data, separators=(',', ':'), sort_keys=True)
    stamp = hashlib.sha256(payload.encode()).hexdigest()[:12]
    cache.set(PAYLOAD_KEY, (payload, stamp), 60 * 60 * 24)
    return payload, stamp
