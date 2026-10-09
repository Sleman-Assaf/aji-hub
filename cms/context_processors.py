# cms/context_processors.py
"""Supplies the universal edit toolbar and edit mode to every template.

Previously only /p/<slug>/ pages offered any editing affordance, so an
administrator saw "Manage content" on some pages and nothing on others. This
resolves the current route to its managing Page (if any) and exposes a single
consistent set of controls, plus an offer to start managing a route that has no
page yet.

Two separate questions are answered here, and conflating them was a bug:

    cms_can_edit   — should the floating toolbar appear on this route?
    cms_edit_mode  — is the reader editing this page right now?

A /p/ page has its own header controls, so it never wants the floating toolbar,
but it very much needs edit mode. Returning early on the excluded prefixes meant
builder pages could not be edited in place at all.
"""
from .content_admin import screen_for
from .models import Page, user_can_edit_cms, user_can_publish_cms
from core.context_processors import once_per_request

#: Routes where a floating edit toolbar would be noise or actively unhelpful.
TOOLBAR_EXCLUDED_PREFIXES = (
    '/admin/', '/auth/', '/p/', '/messaging/', '/static/', '/media/',
)

#: Management and form screens are tools, not content. Offering to "manage" them
#: produced bindings like /brand/manage/, which is meaningless and clutters the
#: pages index.
TOOLBAR_EXCLUDED_SUFFIXES = (
    '/manage/', '/new/', '/edit/', '/delete/', '/settings/', '/reorder/',
)

#: Where edit mode is never offered, whatever the URL says.
EDIT_EXCLUDED_PREFIXES = ('/admin/', '/auth/', '/static/', '/media/')


def _page_for(path):
    """The Page this route renders: bound to the route, or served at /p/<slug>/."""
    page = Page.for_path(path)
    if page is not None:
        return page
    parts = [p for p in path.split('/') if p]
    if len(parts) == 2 and parts[0] == 'p':
        return Page.objects.filter(slug=parts[1]).first()
    return None


@once_per_request
def cms_breadcrumbs(request):
    """The trail for the current route, for every reader and not just editors.

    Separate from the toolbar processor because that one returns early for people
    who cannot edit — breadcrumbs are navigation, so they must not depend on
    permissions.
    """
    path = getattr(request, 'path', '') or ''
    if path.startswith(EDIT_EXCLUDED_PREFIXES):
        return {'crumb_trail': []}
    page = _page_for(path)
    if page is None or not page.show_crumbs:
        return {'crumb_trail': []}
    return {'crumb_trail': page.trail()}


@once_per_request
def cms_toolbar(request):
    path = getattr(request, 'path', '') or ''

    if not user_can_edit_cms(getattr(request, 'user', None)):
        return {'cms_can_edit': False, 'cms_edit_mode': False}

    if path.startswith(EDIT_EXCLUDED_PREFIXES) or path.endswith(TOOLBAR_EXCLUDED_SUFFIXES):
        return {'cms_can_edit': False, 'cms_edit_mode': False}

    live_page = _page_for(path)

    # Edit mode is opt-in per request. Without this an editor would browse the
    # whole site covered in dashed outlines and hover toolbars, which makes the
    # Hub unreadable for the people most likely to be reading it all day.
    edit_mode = bool(live_page) and request.GET.get('edit') == '1'
    # Preview shows the draft exactly as a reader would see it: the draft's
    # content, none of the editing chrome.
    preview = bool(live_page) and request.GET.get('preview') == '1'

    # Editing works on a draft copy, so a half-finished heading is never live.
    # The draft is only started when somebody actually opens the editor —
    # merely previewing must not fork one.
    page = live_page
    draft = None
    if live_page is not None and (edit_mode or preview):
        draft = live_page.open_draft()
        if edit_mode and draft is None:
            draft = live_page.start_draft(request.user)
        if draft is not None:
            page = draft

    show_toolbar = not path.startswith(TOOLBAR_EXCLUDED_PREFIXES)

    ctx = {
        'cms_can_edit': show_toolbar,
        'cms_can_publish': user_can_publish_cms(request.user),
        'cms_bound_page': page if show_toolbar else None,
        # The editing sidebar's own page. It is NOT cms_bound_page: that one is
        # suppressed on /p/ routes because they do not want the *floating
        # toolbar*, and sharing the flag meant a page created from scratch —
        # which is always a /p/ page — opened in edit mode with no panel to edit
        # it from.
        'cms_shell_page': page,
        'cms_bound_path': path,
        # Where this page's records are managed, so the toolbar can offer it
        # instead of the editor having to know which section it hides under.
        'cms_content_screen': screen_for(path) if show_toolbar else None,
        # Sections this route contributes, so the toolbar can report a count
        # even on templates that have not yet added the render tag.
        'cms_bound_section_count': page.sections.count() if (page and show_toolbar) else 0,
        'cms_edit_mode': edit_mode,
        'cms_edit_page': page,
        # The published page, whatever is being rendered. Save and Discard act on
        # this one; the sidebar needs it to know where "Done" goes.
        'cms_live_page': live_page,
        'cms_preview': preview,
        'cms_has_draft': draft is not None,
        'cms_draft_by': getattr(draft, 'draft_started_by', None) if draft else None,
        'cms_draft_at': getattr(draft, 'draft_started_at', None) if draft else None,
    }
    if edit_mode:
        from .palette import catalogue, groups, presets
        ctx['cms_palette'] = catalogue()
        # The order and headings come from palette.py now, rather than being a
        # second list here that had to be kept in step with it by hand.
        ctx['cms_palette_groups'] = groups()
        ctx['cms_presets'] = presets()
    return ctx


@once_per_request
def site_chrome(request):
    """The navigation and the logo, for every page and every reader.

    Separate from cms_toolbar, which returns early for people who cannot edit —
    the navigation is not an editing feature, so it must not depend on
    permissions. It is filtered by audience per item instead.
    """
    from .models import SiteBranding, nav_tree

    user = getattr(request, 'user', None)
    path = getattr(request, 'path', '') or ''
    try:
        nav = nav_tree(user)
        branding = SiteBranding.get()
    except Exception:
        # Before migrations run, or if the tables are unavailable. The header
        # still renders; it simply has no menu, which is better than a 500 on
        # every page of the site.
        return {'site_nav': [], 'site_branding': None, 'nav_active': set()}

    # Which entries to mark as the section the reader is in. The old builder did
    # this in JavaScript from location.pathname; doing it here means the right
    # item is highlighted in the markup the server sends, with no flash.
    active = set()
    for item, kids in nav:
        urls = [k.url for k in kids] + ([item.url] if item.url else [])
        for u in urls:
            if not u or not u.startswith('/'):
                continue
            # '/' would otherwise prefix-match every page on the Hub.
            if path == u or (u != '/' and path.startswith(u)):
                active.add(item.pk)
                break
    return {'site_nav': nav, 'site_branding': branding, 'nav_active': active}
