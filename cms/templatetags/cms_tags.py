# cms/templatetags/cms_tags.py
import re

from django import template
from django.template import TemplateDoesNotExist
from django.utils.html import escape
from django.utils.safestring import mark_safe

from cms.icons import icon_svg
from cms.models import user_can_edit_cms, user_can_publish_cms

register = template.Library()


@register.simple_tag
def cms_icon(name, size=18, stroke=None):
    """Render an icon from the editable library by name.

    Unknown or blank names render nothing, so a section never breaks because an
    icon was renamed. Reads the library first and falls back to the shape
    compiled into the code, which is what keeps navigation and buttons safe from
    a content edit.
    """
    from cms.models import render_icon
    return mark_safe(render_icon(name, size=size, stroke=stroke))


@register.simple_tag
def icon(name, size=18, stroke=None, classes=''):
    """Shorter alias, for the chrome where icons appear constantly."""
    from cms.models import render_icon
    return mark_safe(render_icon(name, size=size, stroke=stroke, classes=classes))


@register.simple_tag
def icon_json():
    """The icon set as JSON, for the parts of the interface built in JavaScript.

    The chat panel, toasts and the nav builder draw their own markup, so they
    need the same library the templates use rather than a second hardcoded copy.

    Kept for anything that still wants it inline. base.html does not: 58 KB of
    icon data in every page was the single largest thing the Hub sent, and it is
    identical on every request, so it is served from /p/icons.js instead and
    cached. See cms.icon_models.icon_payload.
    """
    from cms.icon_models import icon_payload
    return mark_safe(icon_payload()[0])


@register.simple_tag
def icon_json_version():
    """A stamp that changes only when the icon set does, for the script URL."""
    from cms.icon_models import icon_payload
    return icon_payload()[1]


@register.filter
def can_edit_cms(user):
    return user_can_edit_cms(user)


@register.filter
def can_publish_cms(user):
    return user_can_publish_cms(user)


@register.simple_tag(takes_context=True)
def cms_sections(context):
    """Render the CMS sections attached to the current route.

    Drop this into any hardcoded template and that page becomes extensible
    without further code changes — editors add sections from the toolbar.
    """
    from django.template.loader import render_to_string
    from cms.models import Page

    request = context.get('request')
    if request is None:
        return ''
    # cms_edit_page is the draft while editing or previewing, and the live page
    # otherwise. Resolving the route again here would render the published
    # content underneath the editor, so every change would look like it had not
    # saved.
    page = context.get('cms_edit_page') or Page.for_path(request.path)
    if page is None or not page.is_visible_to(request.user):
        return ''
    sections = page.visible_sections(request.user)
    if not sections:
        return ''
    ctx = _host_context(context)
    ctx.update({
        'sections': sections,
        'page': page,
        'request': request,
        'cms_edit_mode': context.get('cms_edit_mode', False),
    })
    return mark_safe(render_to_string('cms/partials/sections.html', ctx))


#: Context keys that must not be carried into a block. `block` is Django's own
#: template-inheritance bookkeeping and passing it on confuses {% block %};
#: the rest are re-set explicitly by every caller anyway.
_HOST_SKIP = {'block', 'sections', 'page', 'section', 'columns', 'inner'}


def _host_context(context):
    """The host view's variables, flattened, so blocks can see them.

    Blocks need the page's own context because a locked feature renders the
    partial that page always rendered. Flattening is wrapped because a context
    can contain lazy objects that raise on access, and one bad variable must not
    take the page down.
    """
    try:
        flat = context.flatten()
    except Exception:
        return {}
    return {k: v for k, v in flat.items() if k not in _HOST_SKIP}


@register.simple_tag(takes_context=True)
def cms_page_body(context, page=None):
    """Render a whole page from its blocks — the builder's output.

    Used by templates that are fully CMS-driven rather than hardcoded pages with
    extra sections appended. With no page given it resolves the one bound to the
    current route, so a template does not need the view to supply it.
    """
    from django.template.loader import render_to_string
    from cms.models import Page

    request = context.get('request')
    if request is None:
        return ''
    if page is None:
        # The draft while editing or previewing; see cms_sections above.
        page = context.get('cms_edit_page') or Page.for_path(request.path)
    if page is None or not page.is_visible_to(request.user):
        return ''
    # The host view's context is carried through, because a locked feature block
    # renders the partial its page always rendered and needs that page's
    # variables. Without this the feature rendered its markup with no data — the
    # People & Culture HR feed came out as a heading with zero cards.
    ctx = _host_context(context)
    ctx.update({
        'page': page,
        'sections': page.visible_sections(request.user),
        'request': request,
        'cms_edit_mode': context.get('cms_edit_mode', False),
    })
    return mark_safe(render_to_string('cms/partials/page_body.html', ctx))


@register.simple_tag(takes_context=True)
def cms_block(context, section):
    """Render one block, recursing into a columns row.

    Each block gets its live data from cms.providers, so a block behaves the
    same whether it sits on the homepage, inside a column, or on a page created
    five minutes ago. The editor chrome is wrapped around it here rather than in
    every section template.
    """
    from django.template.loader import render_to_string
    from cms import providers, widgets

    request = context.get('request')
    edit = bool(context.get('cms_edit_mode'))

    # The host page's context is carried into every block, because a locked
    # feature renders the partial its page always rendered and needs that page's
    # variables. Carrying it for all block types rather than just features means
    # a feature nested inside a columns row still receives it. Block-specific
    # values are applied afterwards, so they always win over a host name.
    ctx = _host_context(context)
    if section.type == 'feature':
        ctx['widget_template'] = widgets.widget_template(section)

    ctx.update({
        'section': section,
        'request': request,
        'cms_edit_mode': edit,
    })
    ctx.update(providers.context_for(section, request))
    if section.is_row:
        ctx['columns'] = section.columns_for(getattr(request, 'user', None))

    try:
        inner = render_to_string(section.template_path, ctx, request=request)
    except TemplateDoesNotExist:
        if not edit:
            return ''
        inner = ('<div class="card" style="color:var(--muted);font-size:12.5px">'
                 'This block type has no template yet: '
                 + escape(section.type) + '</div>')

    return mark_safe(render_to_string('cms/partials/block.html', {
        'section': section,
        'inner': mark_safe(inner),
        'cms_edit_mode': edit,
        'request': request,
    }))


#: Matches the company name so it can be styled without the editor having to
#: type markup. Word-bounded so "AJineers" and "AJi Hub" are left alone.
_AJI_RE = re.compile(r'\bAJi\b')


@register.filter
def aji(text):
    """Wrap the company name in the class that stops it being upper-cased.

    Section headings are upper-cased by .sec-head h2, which would render "AJI".
    Editors type plain "AJi" and this keeps the lowercase i, so the rule the
    whole site follows survives content editing.
    """
    if not text:
        return ''
    return mark_safe(_AJI_RE.sub('<span class="kc">AJi</span>', escape(text)))


@register.filter
def safe_html(section):
    """Render a custom-HTML section.

    Unescaped only when a superuser saved it (Section.html_approved). Otherwise
    the markup is shown escaped, so a content editor cannot ship script to
    every employee by typing it into a text box.
    """
    body = section.body or ''
    if getattr(section, 'html_approved', False):
        return mark_safe(body)
    if not body:
        return ''
    return mark_safe(
        '<div class="muted" style="font-size:12.5px;margin-bottom:8px">'
        'Pending review — shown as text until a system administrator approves it.'
        '</div><pre style="white-space:pre-wrap;font-size:12px;margin:0">'
        + escape(body) + '</pre>'
    )


# NOTE: these are simple_tags rather than inclusion_tags on purpose.
# @inclusion_tag calls Context.new() -> copy(), and Django 5.0's
# BaseContext.__copy__ does `copy(super())`, which returns a bare super object
# on Python 3.14 and blows up. render_to_string takes a plain dict and never
# copies the caller's context, so it sidesteps that entirely.
@register.simple_tag
def cms_news_feed(limit=4):
    from django.template.loader import render_to_string
    from content.models import NewsArticle
    # -created_at breaks the tie between articles sharing a date; without it the
    # newest of the day sorts last and falls off a short list. See news_list in
    # cms/providers.py, which had the same bug.
    articles = (NewsArticle.objects.filter(published=True)
                .order_by('-pinned', '-date', '-created_at')[:_clamp(limit, 4)])
    return mark_safe(render_to_string('cms/partials/news_feed.html',
                                      {'articles': articles}))


@register.simple_tag
def cms_policy_list(limit=6):
    from django.template.loader import render_to_string
    from portal.models import Policy
    policies = Policy.objects.all()[:_clamp(limit, 6)]
    return mark_safe(render_to_string('cms/partials/policy_list.html',
                                      {'policies': policies}))


def _clamp(value, default):
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n or default, 20))


@register.simple_tag(takes_context=True)
def head_hidden(context, section):
    """Should this page heading step aside?

    A landing heading must not repeat itself once the reader has navigated into
    something: /policies/?cat=… showed "Governance / Policies & SOPs" above
    "Governance / Cyber Security Governance Framework" — the same label twice and
    two competing titles.

    The block names a context variable in `hide_when`; when the view has set that
    variable, the heading does not render and the page leads with its subject. It
    is configuration rather than a rule per page, so any route can opt in.
    """
    key = (section.config or {}).get('hide_when') or ''
    if not key:
        return False
    return bool(context.get(key))


@register.simple_tag(takes_context=True)
def sec_link_ok(context, section):
    """Whether this block's heading link should be shown to the current reader.

    A section heading can carry one link on its own row -- "Manage updates",
    "View all". Some of those lead somewhere only HR may go, and a link that
    everyone can see but only HR can follow is worse than no link.

    Without this the People & Culture "Manage updates" link could not live in the
    heading row at all: it had to sit in its own right-aligned div above the
    content, which is what made it read as floating loose on the page.
    """
    cfg = section.config or {}
    if not cfg.get('link_url'):
        return False
    audience = cfg.get('link_audience') or ''
    if not audience:
        return True
    from cms.models import Audience, audience_allows
    # "Specific groups" needs a group list, and config has nowhere to hold one.
    # Refused rather than passed through as None, which audience_allows would try
    # to call .filter() on.
    if audience == Audience.GROUPS:
        return False
    request = context.get('request')
    user = getattr(request, 'user', None) if request else None
    return audience_allows(audience, None, user)


@register.simple_tag(takes_context=True)
def page_actions(context, section):
    """The header actions this reader may see.

    Each action is a row with its own link, icon and audience, so "New article"
    can be HR-only and "Manage brand files" Branding-only without either being
    hardcoded behind a template condition.
    """
    request = context.get('request')
    user = getattr(request, 'user', None)
    try:
        return section.visible_items(user)
    except Exception:
        return []


@register.filter
def tab_body(text, section):
    """A tab panel's markup, under the same gate as a Custom HTML block.

    Panels hold real markup — cards, lists, links — so they cannot simply be
    escaped and still look right. They are rendered raw only once a system
    administrator has saved the block, because this content reaches every
    employee; until then the markup is shown as text rather than run.
    """
    if not text:
        return ''
    if getattr(section, 'html_approved', False):
        return mark_safe(text)
    return mark_safe(
        '<div class="muted" style="font-size:12.5px;margin-bottom:8px">'
        'Pending review — shown as text until a system administrator approves '
        'this block.</div><pre style="white-space:pre-wrap;font-size:12px;margin:0">'
        + escape(text) + '</pre>')


@register.filter
def dictget(mapping, key):
    """Look up a dict by a variable key. Templates cannot do this natively, and
    the palette needs it to walk its groups in a defined order."""
    try:
        return mapping.get(key) or []
    except Exception:
        return []


@register.filter
def cfg(section, key):
    """Read a per-type setting out of Section.config with no template errors."""
    try:
        return (section.config or {}).get(key, '')
    except Exception:
        return ''


@register.simple_tag(takes_context=True)
def feature_text(context, section, key, tag='span', css=''):
    """A word or line inside a content panel, editable on the page.

    Panels render records, but the headings and descriptions around those records
    were typed into the template, so nobody could change them without a code
    change -- which is most of what "this panel is locked" actually meant.

    The default lives in widgets.LABELS rather than here, so the same string is
    what the settings drawer lists and what renders when nothing has overridden
    it. An override is stored on the block itself, under config['text'], so a
    panel used on two pages can say something different on each.

    Only keys the widget declares can be written; see inline_save.
    """
    from cms.widgets import labels_for, widget_key

    wkey = widget_key(section)
    declared = labels_for(wkey)
    default, hint = declared.get(key, ('', ''))
    stored = ((section.config or {}).get('text') or {}).get(key)

    if stored is None:
        # The default is authored in widgets.py by whoever wrote the panel, so it
        # may carry entities like &amp; and is rendered as written.
        value = default
    else:
        # Anything an editor typed is escaped. This is rendered unescaped into
        # every page the panel appears on, so a heading is exactly the wrong
        # place to trust stored text — the string arrives from the browser as
        # plain textContent and nothing sanitises it on the way in.
        value = escape(stored)

    tag = str(tag) if str(tag).isalnum() else 'span'
    cls = f' class="{escape(css)}"' if css else ''

    if not context.get('cms_edit_mode'):
        return mark_safe(f'<{tag}{cls}>{value}</{tag}>') if value else ''

    # In edit mode it is always rendered, even when blank, or a line somebody
    # cleared could never be got back.
    attrs = (f' data-ed-field="txt_{escape(key)}" contenteditable="true"'
             f' data-ed-placeholder="{escape(hint)}"')
    return mark_safe(f'<{tag}{cls}{attrs}>{value}</{tag}>')


@register.simple_tag(takes_context=True)
def feature_rich(context, section, key, tag='div', css=''):
    """Several lines inside a content panel -- a list, or a run of paragraphs.

    The same idea as feature_text, for content that is markup rather than one
    string: the bullets under a policy, the steps in a process. Splitting those
    into one label per line would have frozen how many lines there are, and the
    point is that somebody can add a fourth bullet without a code change.

    What comes back from the browser is reduced to the allow-list before it is
    stored, the same way a rich text block's body is, so what reaches the
    database is already safe to render.
    """
    from cms.widgets import labels_for, widget_key

    declared = labels_for(widget_key(section))
    default, hint = declared.get(key, ('', ''))
    stored = ((section.config or {}).get('text') or {}).get(key)
    # Stored markup was sanitised on the way in; the default is authored in
    # widgets.py. Neither is escaped here, which is exactly why the save path
    # sanitises rather than trusting.
    value = default if stored is None else stored

    tag = str(tag) if str(tag).isalnum() else 'div'
    cls = f' class="{escape(css)}"' if css else ''

    if not context.get('cms_edit_mode'):
        return mark_safe(f'<{tag}{cls}>{value}</{tag}>') if value else ''

    attrs = (f' data-ed-field="txt_{escape(key)}" data-ed-rich contenteditable="true"'
             f' data-ed-placeholder="{escape(hint)}"')
    return mark_safe(f'<{tag}{cls}{attrs}>{value}</{tag}>')


@register.simple_tag(takes_context=True)
def editable_icon(context, owner, name, size=18, stroke=None, kind='block'):
    """An icon that can be changed by clicking it, when edit mode is on.

    Changing a tile's icon used to be: select the block, find the Icon field in
    the properties panel, open the picker, choose, come back. The icon on the
    page is the button now.

    `kind` says what the primary key belongs to — a block or one of its rows —
    so the click posts to the right endpoint rather than guessing from the DOM.
    Outside edit mode this renders exactly what cms_icon renders, no wrapper.
    """
    from cms.models import render_icon

    svg = render_icon(name, size=size, stroke=stroke)
    if not context.get('cms_edit_mode'):
        return mark_safe(svg)

    pk = getattr(owner, 'pk', owner)
    # An icon that has never been set still needs somewhere to click, so an
    # empty one falls back to a faint placeholder rather than nothing at all.
    body = svg or render_icon('plus', size=size, stroke=stroke)
    return mark_safe(
        f'<span data-ed-icon="{escape(str(kind))}" data-ed-icon-pk="{escape(str(pk))}"'
        f' data-ed-icon-value="{escape(name or "")}"'
        f' title="Click to change this icon"'
        f' class="{"ed-ico-empty" if not svg else ""}">{body}</span>')


@register.filter
def doc_format(name):
    """What kind of file this is: {ext, icon, label, previewable}.

    A list of documents used to draw the same blank page for every one of them,
    so a workbook, a Word form and a PDF were indistinguishable until you opened
    them. Used as `{% with fmt=p.file.name|doc_format %}`.

    Imported inside the function rather than at module level: this is the CMS's
    tag library and portal.documents pulls in the whole document stack, which has
    no business loading every time a template renders a heading.
    """
    from portal.documents import format_of
    return format_of(name)
