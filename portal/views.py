# portal/views.py
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from .models import Policy, BrandAssetCategory, OnboardingSection
from core.models import (is_intranet_admin, user_can_manage_branding,
                         user_can_manage_policies)
# Where a save should land. Every handler used to redirect to the one management
# view that existed before it was split in two, so saving a wellness card sent
# you to the Onboarding screen.
from cms.content_admin import back_to


@login_required
def policies(request):
    from .models import PolicyCategory
    cat_id   = request.GET.get('cat', '')
    q        = request.GET.get('q', '').strip()
    folder = None
    crumbs = []
    subfolders = []

    # Prefetched, then filtered in Python: `allows` asks about this reader's
    # groups and about named individuals, and doing that per row would be two
    # queries per library and per document.
    everything = (PolicyCategory.objects
                  .prefetch_related('policies__allowed_groups',
                                    'policies__allowed_users',
                                    'allowed_groups', 'allowed_users'))
    # `visible_to`, not `allows`: a request-based library is listed to everyone
    # precisely so it can be asked for. `allows` still guards the contents, and
    # the two answers differing is the whole of that feature.
    categories = [c for c in everything if c.visible_to(request.user)]
    for c in categories:
        # Worked out here because a template cannot call a method with an
        # argument. '' for anything the reader can already open.
        c.ask_state = c.request_state(request.user)

    selected_cat = None
    cat_policies = None

    def readable(policies_iter):
        """Only what this reader may open, each told whether they may keep it.

        `may_download` takes the user, and a template cannot call a method with
        an argument — so the answer is worked out here and hung on the object.
        """
        out = []
        for policy in policies_iter:
            if not policy.allows(request.user):
                continue
            policy.can_download = policy.may_download(request.user)
            out.append(policy)
        return out

    if cat_id:
        # `allows`, not merely visible. `categories` now holds request-based
        # libraries this reader can see but not read, and picking one out of that
        # list would have opened it as an *empty* library — which is exactly the
        # thing the note below says must never happen.
        selected_cat = next((c for c in categories
                             if str(c.pk) == str(cat_id)
                             and c.allows(request.user)), None)
        # A library they may not open is not "empty" and not an error either —
        # it is simply not theirs, and behaves exactly as one that is not there.
        if selected_cat is not None:
            # ── which folder, and the trail back out ──
            from .models import PolicyFolder
            raw = (request.GET.get('folder') or '').strip()
            if raw.isdigit():
                folder = (PolicyFolder.objects
                          .filter(pk=int(raw), library=selected_cat)
                          .select_related('parent').first())
            else:
                folder = None
            crumbs = (folder.ancestors() + [folder]) if folder else []
            subfolders = list(selected_cat.folders
                              .filter(parent=folder)
                              # distinct on both: two counts over different
                              # reverse relations join twice and would otherwise
                              # multiply each other's totals.
                              .annotate(n_docs=Count('documents', distinct=True),
                                        n_subs=Count('children', distinct=True))
                              .order_by('order', 'name'))
            # A search looks through the whole library rather than the folder in
            # front of you: somebody searching has stopped browsing.
            in_scope = (selected_cat.policies.all() if q
                        else selected_cat.policies.filter(folder=folder))
            cat_policies = readable(in_scope)
            if q:
                cat_policies = [p for p in cat_policies if q.lower() in p.title.lower()]

    uncategorised = readable(Policy.objects
                             .filter(category__isnull=True)
                             .prefetch_related('allowed_groups', 'allowed_users'))

    is_policies_member = user_can_manage_policies(request.user)
    # Settings belong to whoever set the library up, so the link only appears
    # for them — a colleague with the same role sees the library, not its knobs.
    can_manage_library = (selected_cat.can_manage(request.user)
                          if selected_cat is not None else False)

    return render(request, 'portal/policies.html', {
        # What this section is called, read from the CMS page bound to the route
        # rather than typed into the template. The back link said "Policies &
        # SOPs" while the page heading and the navigation both said "Policies &
        # Manuals", because the first was a string in the markup and the other
        # two were rows in the database. One name, one place to change it.
        'section_name':        _bound_page_title('/policies/', 'SOPs & Manuals'),
        'categories':          categories,
        'selected_cat':        selected_cat,
        'can_manage_library':  can_manage_library,
        'folder':              folder,
        'crumbs':              crumbs,
        'subfolders':          subfolders,
        'cat_policies':        cat_policies,
        'uncategorised':       uncategorised,
        'q':                   q,
        'is_policies_member':  is_policies_member,
        # Ends the breadcrumb on the category the reader opened.
        'crumb_leaf':          selected_cat.name if selected_cat else '',
    })


@login_required
def brand_files(request):
    import json as _json
    from .models import BrandSection, BrandColour, brand_files_visible_to

    # The whole area can be closed to people it is not for. 404 rather than a
    # refusal: a page that says "you may not see this" has still told you it
    # exists, and the navigation entry is hidden on the same rule.
    if not brand_files_visible_to(request.user):
        raise Http404

    # Prefetched, then filtered in Python: allows() asks about this reader's
    # groups and about named individuals, and doing that per row would be two
    # queries per section and per tile. Same shape as the Policies page.
    everything = (BrandSection.objects
                  .prefetch_related('templates__files__group',
                                    'templates__allowed_groups', 'templates__allowed_users',
                                    'allowed_groups', 'allowed_users'))
    sections = [s for s in everything if s.allows(request.user)]
    sections_dict = {s.section_type: s for s in sections}
    tpl_section   = sections_dict.get('templates')
    asset_section = sections_dict.get('brand_assets')

    def build_templates(section):
        if not section:
            return []
        result = []
        for t in section.templates.all():
            # A tile the reader may not see is not drawn, and its files are
            # never listed — the download route checks the same rule again.
            if not t.allows(request.user):
                continue
            from . import documents
            files_json = _json.dumps([
                {
                    'id':      f.pk,
                    'label':   f.label,
                    # `has_file`, not the file's own URL. The download already
                    # goes through a guarded route; printing the /media/ address
                    # into the page as well only published it.
                    'has_file': bool(f.file),
                    # Whether the viewer can show it. A .docx template can only
                    # be downloaded, and saying so up front beats a Read button
                    # that opens on an apology.
                    'kind':    documents.kind_of(f.file.name) if f.file else '',
                    'ext':     f.ext(),
                    'version': f.version,
                    'group':   f.group.name if f.group else None,
                }
                for f in t.files.all()
            ])
            result.append({'name': t.name, 'description': t.description,
                           'icon': t.icon, 'files_json': files_json})
        return result

    templates    = build_templates(tpl_section)
    brand_assets = build_templates(asset_section)

    colors = list(BrandColour.objects.all())
    if not colors:
        colors = [
            type('C', (), {'name':'Yellow','hex':'#FCD700','ref':'Pantone 012 C','light':False})(),
            type('C', (), {'name':'Black', 'hex':'#000000','ref':'C0 M0 Y0 K100','light':False})(),
            type('C', (), {'name':'White', 'hex':'#FFFFFF','ref':'C0 M0 Y0 K0',  'light':True})(),
        ]

    is_branding = user_can_manage_branding(request.user)

    return render(request, 'portal/brand_files.html', {
        'templates':          templates,
        'brand_assets':       brand_assets,
        'colors':             colors,
        'is_branding_member': is_branding,
    })


@login_required
def onboarding(request):
    from .models import OnboardingHandbookItem, CEOLetter, EmployeeHandbookFile
    tab = request.GET.get('tab', 'conduct')

    ceo_letter, _      = CEOLetter.objects.get_or_create(pk=1)
    handbook_file, _   = EmployeeHandbookFile.objects.get_or_create(pk=1)

    # Records, so the label, icon and order are editable. These were a list here
    # with their icons chosen by an if/elif chain in the template.
    from .models import ExploreTab
    ob_tab_rows = list(ExploreTab.objects.filter(
        group=ExploreTab.Group.ONBOARDING, is_active=True))
    OB_TABS = [(t.key, t.label, t.icon) for t in ob_tab_rows]
    if tab not in [t.key for t in ob_tab_rows] and ob_tab_rows:
        tab = ob_tab_rows[0].key

    all_items = OnboardingHandbookItem.objects.all()
    PANEL_DATA = {}
    for tab_id, _, _ in OB_TABS:
        PANEL_DATA[tab_id] = [
            (item.title, item.body) for item in all_items if item.tab == tab_id
        ]

    is_hr = request.user.is_superuser or request.user.groups.filter(name='HR').exists()

    return render(request, 'portal/onboarding.html', {
        'all_panels':   PANEL_DATA,
        'all_panels_list': [(tab_id, PANEL_DATA.get(tab_id, [])) for tab_id, _, _ in OB_TABS],
        'tab':          tab,
        'ob_tabs':      ob_tab_rows,
        'panel_items':  PANEL_DATA.get(tab, []),
        'is_hr':        is_hr,
        'ceo_letter':   ceo_letter,
        'handbook_file': handbook_file,
        # The key values, expertise areas and statistics used to be literal lists
        # here. They are CMS records now — a Value cards block, an Icon tiles
        # block and a Text-with-statistics block on the Onboarding page — so they
        # can be edited, reordered, given their own icons or hidden without a
        # deploy. The statistics in particular existed twice, here and as fallback
        # markup in the template, and the two were free to disagree.
    })


# ── Brand Management (Branding group only) ───────────────────────────────────

from functools import wraps
from django.http import Http404, FileResponse, JsonResponse
from django.contrib import messages as dj_messages
from django.urls import reverse
from django.views.decorators.http import require_POST


def _tell(request, text, level='success'):
    """Queue a flash banner — unless the browser saved in the background.

    The management screens post without rebuilding the page (see
    includes/manage_forms_js.html) and show their own confirmation. A banner
    queued for one of those saves is never rendered, so it sits in the session
    and surfaces on top of the *next* full page load, by which time it is stale
    and refers to something the reader has forgotten doing. Ten quiet saves used
    to mean ten banners the next time the page was opened.

    Saves that do reload the page do not send the header, so they still get their
    banner — that is the one case where it is read at the moment it means
    something.
    """
    if request.headers.get('X-Quiet-Save') == '1':
        return
    getattr(dj_messages, level)(request, text)


def branding_required(view_func):
    """Gate the Brand Files and AJi Booth management screens.

    Shares user_can_manage_branding with the `is_branding_member` template flag,
    so the Manage buttons and the views behind them can never disagree — keeping
    the two rules in step by hand is what put a button on the Brand Files
    heading that the view then refused.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            from django.shortcuts import redirect
            return redirect('login')
        if user_can_manage_branding(request.user):
            return view_func(request, *args, **kwargs)
        raise Http404
    return wrapper


def _access_from_post(request, obj, allow_inherit=False):
    """Apply an access choice and its lists from a posted form.

    Shared by the page, section and tile forms, which all offer the same three
    or two options and the same two pickers.
    """
    from .models import DocAccess, INHERIT
    choices = {DocAccess.EVERYONE, DocAccess.RESTRICTED}
    if allow_inherit:
        choices.add(INHERIT)
    access = request.POST.get('access')
    obj.access = access if access in choices else (
        INHERIT if allow_inherit else DocAccess.EVERYONE)
    obj.save(update_fields=['access'])
    obj.allowed_groups.set(
        [g for g in request.POST.getlist('groups') if str(g).isdigit()])
    obj.allowed_users.set(
        [u for u in request.POST.getlist('people') if str(u).isdigit()])


@branding_required
def brand_manage(request):
    """Main brand management dashboard."""
    from django.contrib.auth.models import Group
    from .models import BrandSection, BrandColour, BrandVisibility
    sections     = BrandSection.objects.prefetch_related(
        'templates__groups', 'templates__files__group',
        'templates__allowed_groups', 'templates__allowed_users',
        'allowed_groups', 'allowed_users',
    ).all()
    colours      = BrandColour.objects.all()
    return render(request, 'portal/brand_manage.html', {
        'sections':         sections,
        'colours':          colours,
        # Who can see Brand Files, and who sees it in the menu.
        'visibility':       BrandVisibility.get(),
        'all_groups':       Group.objects.order_by('name'),
    })


@branding_required
@require_POST
def brand_visibility_save(request):
    """Who may reach Brand Files at all, and therefore see it in the menu."""
    from .models import BrandVisibility
    _access_from_post(request, BrandVisibility.get())
    _tell(request, 'Saved who can see Brand Files.')
    return redirect(reverse('brand_manage') + '#visibility')


@branding_required
@require_POST
def brand_section_access(request, pk):
    """Who may see one section of Brand Files."""
    from .models import BrandSection
    section = get_object_or_404(BrandSection, pk=pk)
    _access_from_post(request, section)
    _tell(request, f'Saved who can see “{section.name}”.')
    return redirect(reverse('brand_manage') + f'#section-{section.pk}')


@branding_required
@require_POST
def brand_template_access(request, pk):
    """Who may see one tile, within what its section already permits."""
    from .models import BrandTemplate
    tmpl = get_object_or_404(BrandTemplate.objects.select_related('section'), pk=pk)
    _access_from_post(request, tmpl, allow_inherit=True)
    _tell(request, f'Saved who can see “{tmpl.name}”.')
    return redirect(reverse('brand_manage') + f'#section-{tmpl.section_id}')


@branding_required
def brand_people_search(request):
    """Colleagues for the access pickers. Shares the Hub's one search."""
    from core.people import search_people
    return JsonResponse({'users': search_people(request.GET.get('q', ''))})




@branding_required
def brand_section_add(request):
    if request.method == 'POST':
        from .models import BrandSection
        name         = request.POST.get('name', '').strip()
        section_type = request.POST.get('section_type', 'other')
        order        = int(request.POST.get('order', 99))
        if name:
            BrandSection.objects.create(name=name, section_type=section_type, order=order)
            dj_messages.success(request, f'Section "{name}" added.')
        return redirect('brand_manage')
    return redirect('brand_manage')


@branding_required
def brand_section_delete(request, pk):
    from .models import BrandSection
    sec = get_object_or_404(BrandSection, pk=pk)
    if request.method == 'POST':
        name = sec.name
        sec.delete()
        dj_messages.success(request, f'Section "{name}" deleted.')
    return redirect('brand_manage')


@branding_required
def brand_template_add(request, section_pk):
    """Add a tile to this section, or update one already in it.

    One view for both, because they are the same request with and without a pk —
    and because there was no way to change a tile at all before this: its name,
    its label and now its icon could be typed once when it was created and never
    corrected without deleting the tile and its files.
    """
    from .models import BrandSection, BrandTemplate
    section = get_object_or_404(BrandSection, pk=section_pk)
    if request.method == 'POST':
        pk          = request.POST.get('pk', '').strip()
        name        = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        icon        = request.POST.get('icon', '').strip()[:60]
        order       = int(request.POST.get('order', 99) or 99)
        if name:
            if pk:
                # Scoped to this section, so a pk from elsewhere cannot be used
                # to edit a tile the URL does not belong to.
                BrandTemplate.objects.filter(pk=pk, section=section).update(
                    name=name, description=description, icon=icon, order=order)
                _tell(request, f'Template "{name}" updated.')
            else:
                BrandTemplate.objects.create(
                    section=section, name=name, description=description,
                    icon=icon, order=order)
                _tell(request, f'Template "{name}" added to {section.name}.')
    return redirect('brand_manage')


@branding_required
def brand_template_delete(request, pk):
    from .models import BrandTemplate
    tmpl = get_object_or_404(BrandTemplate, pk=pk)
    if request.method == 'POST':
        name = tmpl.name
        tmpl.delete()
        dj_messages.success(request, f'Template "{name}" deleted.')
    return redirect('brand_manage')


@branding_required
def brand_file_upload(request, template_pk):
    from .models import BrandTemplate, BrandTemplateFile, BrandFileGroup
    tmpl = get_object_or_404(BrandTemplate, pk=template_pk)
    if request.method == 'POST' and request.FILES.get('file'):
        label    = request.POST.get('label', '').strip()
        version  = request.POST.get('version', '').strip()
        group_id = request.POST.get('group', '').strip()
        f        = request.FILES['file']
        if not label:
            label = f.name

        group = None
        if group_id:
            group = BrandFileGroup.objects.filter(pk=group_id, template=tmpl).first()

        BrandTemplateFile.objects.create(
            template=tmpl, group=group, label=label, file=f, version=version
        )
        dj_messages.success(request, f'File "{label}" uploaded to {tmpl.name}.')
    return redirect('brand_manage')


@branding_required
def brand_group_add(request, template_pk):
    """Add a new file group (subdirectory) inside a template."""
    from .models import BrandTemplate, BrandFileGroup
    tmpl = get_object_or_404(BrandTemplate, pk=template_pk)
    if request.method == 'POST':
        name  = request.POST.get('name', '').strip()
        order = int(request.POST.get('order', 99) or 99)
        if name:
            BrandFileGroup.objects.create(template=tmpl, name=name, order=order)
            dj_messages.success(request, f'Group "{name}" added to {tmpl.name}.')
    return redirect('brand_manage')


@branding_required
def brand_group_delete(request, pk):
    """Delete a group — files inside it become ungrouped (not deleted)."""
    from .models import BrandFileGroup
    grp = get_object_or_404(BrandFileGroup, pk=pk)
    if request.method == 'POST':
        name = grp.name
        grp.delete()
        dj_messages.success(request, f'Group "{name}" removed. Its files are now ungrouped.')
    return redirect('brand_manage')


@branding_required
def brand_file_delete(request, pk):
    from .models import BrandTemplateFile
    import os
    bf = get_object_or_404(BrandTemplateFile, pk=pk)
    if request.method == 'POST':
        label = bf.label
        # Delete physical file
        if bf.file:
            try:
                if os.path.isfile(bf.file.path):
                    os.remove(bf.file.path)
            except Exception:
                pass
        bf.delete()
        dj_messages.success(request, f'File "{label}" deleted.')
    return redirect('brand_manage')


def brand_file_download(request, pk):
    """Serve a brand file for download, to anybody allowed to see its tile."""
    from .models import BrandTemplateFile, brand_files_visible_to
    import os
    if not request.user.is_authenticated:
        return redirect('login')
    bf = get_object_or_404(
        BrandTemplateFile.objects.select_related('template__section'), pk=pk)
    # Checked again here, not only when the page was drawn. Hiding a tile only
    # stops it being offered; the address of the file underneath is a guessable
    # number, so the rule has to hold at the point the bytes are served.
    if not (brand_files_visible_to(request.user)
            and bf.template is not None and bf.template.allows(request.user)):
        raise Http404
    if not bf.file:
        raise Http404('File not yet uploaded.')
    try:
        response = FileResponse(
            open(bf.file.path, 'rb'),
            as_attachment=True,
            filename=os.path.basename(bf.file.name),
        )
        return response
    except FileNotFoundError:
        raise Http404('File not found on disk.')


# ── Colour & Booth management ─────────────────────────────────────────────────

@branding_required
def brand_colour_save(request):
    """POST — add or update a colour."""
    from .models import BrandColour
    if request.method == 'POST':
        pk    = request.POST.get('pk', '').strip()
        name  = request.POST.get('name', '').strip()
        hex_  = request.POST.get('hex', '').strip()
        ref   = request.POST.get('ref', '').strip()
        light = bool(request.POST.get('light'))
        order = int(request.POST.get('order', 99))
        if name and hex_:
            if pk:
                BrandColour.objects.filter(pk=pk).update(
                    name=name, hex=hex_, ref=ref, light=light, order=order)
                _tell(request, f'Colour "{name}" updated.')
            else:
                BrandColour.objects.create(
                    name=name, hex=hex_, ref=ref, light=light, order=order)
                _tell(request, f'Colour "{name}" added.')
    return redirect(back_to('brand-colours'))


@branding_required
def brand_colour_delete(request, pk):
    from .models import BrandColour
    c = get_object_or_404(BrandColour, pk=pk)
    if request.method == 'POST':
        dj_messages.success(request, f'Colour "{c.name}" deleted.')
        c.delete()
    return redirect(back_to('brand-colours'))


@branding_required
def booth_album_save(request):
    """POST — add or update a booth album."""
    from .models import BoothAlbum
    if request.method == 'POST':
        pk          = request.POST.get('pk', '').strip()
        name        = request.POST.get('name', '').strip()
        period      = request.POST.get('period', '').strip()
        tag         = request.POST.get('tag', 'other')
        order       = int(request.POST.get('order', 99) or 99)
        # photo_count is not on this form any more — the album counts its own
        # files. Reading it from the post would land the default, 0, on every
        # album whose name was edited.
        if name:
            if pk:
                album = get_object_or_404(BoothAlbum, pk=pk)
                album.name        = name
                album.period      = period
                album.tag         = tag
                album.order       = order
                if 'cover_photo' in request.FILES:
                    album.cover_photo = request.FILES['cover_photo']
                album.save()
                dj_messages.success(request, f'Album "{name}" updated.')
            else:
                album = BoothAlbum(name=name, period=period, tag=tag, order=order)
                if 'cover_photo' in request.FILES:
                    album.cover_photo = request.FILES['cover_photo']
                album.save()
                dj_messages.success(request, f'Album "{name}" added.')
    return redirect('booth_manage')


@branding_required
def booth_album_delete(request, pk):
    from .models import BoothAlbum
    album = get_object_or_404(BoothAlbum, pk=pk)
    if request.method == 'POST':
        dj_messages.success(request, f'Album "{album.name}" deleted.')
        album.delete()
    return redirect('booth_manage')


# ── Booth Photo Management ────────────────────────────────────────────────────

@branding_required
def booth_manage(request):
    """AJi Booth album management (add / edit / delete albums) — separate
    from Brand Files management."""
    from .models import BoothAlbum
    booth_albums = BoothAlbum.objects.all()
    return render(request, 'portal/booth_manage.html', {
        'booth_albums':      booth_albums,
        'album_tag_choices': BoothAlbum.TAGS,
    })


@login_required
def booth_wall(request):
    """AJi Booth — the gallery of all photo/video albums. All logged-in users."""
    from .models import BoothAlbum
    booth_albums = BoothAlbum.objects.all()
    is_branding = user_can_manage_branding(request.user)
    return render(request, 'portal/booth_wall.html', {
        'booth_albums':       booth_albums,
        'is_branding_member': is_branding,
    })


@login_required
def booth_album_view(request, pk):
    """Public view of a booth album's photos & videos — all logged-in users."""
    from .models import BoothAlbum
    album = get_object_or_404(BoothAlbum, pk=pk)
    photos = album.photos.all()
    videos = album.videos.all()
    is_branding = user_can_manage_branding(request.user)
    return render(request, 'portal/booth_album_view.html', {
        'album':         album,
        'photos':        photos,
        'videos':        videos,
        'is_branding_member': is_branding,
    })


def _remove_media(queryset, field):
    """Delete rows and their files. Returns how many went.

    The file is removed before the row, and a file that has already vanished from
    disk does not stop the row going — otherwise a half-deleted album can never
    be tidied up.

    No decorator. This carried @branding_required, which is for views: it reads
    `request.user` off its first argument, so every call — `_remove_media(qs,
    'image')` — died on `'QuerySet' object has no attribute 'user'`. Bulk delete
    has therefore never once worked; it 500'd on the first selection anybody made.
    The permission check belongs on the view that calls this, which has it.
    """
    import os

    gone = 0
    for obj in queryset:
        handle = getattr(obj, field, None)
        if handle:
            try:
                if os.path.isfile(handle.path):
                    os.remove(handle.path)
            except Exception:
                pass
        obj.delete()
        gone += 1
    return gone


@branding_required
def booth_album_photos(request, pk):
    """View and manage photos & videos inside a single booth album.

    The decorator belongs here and had slipped one function down the file onto
    the `_remove_media` helper, which broke two things at once. Bulk delete
    always failed, because the decorator read `request.user` off the queryset it
    was handed. And this view — which uploads media, deletes media, empties an
    album and sets its cover — was left with no permission check at all, open to
    anybody who knew the URL, signed in or not, while every other booth
    management view required the Branding group.
    """
    from .models import BoothAlbum, BoothPhoto, BoothVideo
    album = get_object_or_404(BoothAlbum, pk=pk)
    photos = album.photos.all()
    videos = album.videos.all()

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'upload' and request.FILES.getlist('media'):
            caption = request.POST.get('caption', '').strip()
            photo_ct, video_ct = 0, 0
            for f in request.FILES.getlist('media'):
                ext = f.name.rsplit('.', 1)[-1].lower() if '.' in f.name else ''
                if ext in BoothVideo.ALLOWED_EXTENSIONS:
                    BoothVideo.objects.create(album=album, video=f, caption=caption, uploaded_by=request.user)
                    video_ct += 1
                else:
                    BoothPhoto.objects.create(album=album, image=f, caption=caption)
                    photo_ct += 1
            if photo_ct:
                album.photo_count = album.photos.count()
                album.save(update_fields=['photo_count'])
            parts = []
            if photo_ct:
                parts.append(f'{photo_ct} photo{"s" if photo_ct != 1 else ""}')
            if video_ct:
                parts.append(f'{video_ct} video{"s" if video_ct != 1 else ""}')
            dj_messages.success(request, f'{" and ".join(parts)} uploaded to "{album.name}".')
            return redirect('booth_album_photos', pk=pk)

        elif action == 'delete':
            photo_id = request.POST.get('photo_id')
            photo = get_object_or_404(BoothPhoto, pk=photo_id, album=album)
            import os
            if photo.image:
                try:
                    if os.path.isfile(photo.image.path):
                        os.remove(photo.image.path)
                except Exception:
                    pass
            photo.delete()
            album.photo_count = album.photos.count()
            album.save(update_fields=['photo_count'])
            dj_messages.success(request, 'Photo deleted.')
            return redirect('booth_album_photos', pk=pk)

        elif action == 'delete_video':
            video_id = request.POST.get('video_id')
            video = get_object_or_404(BoothVideo, pk=video_id, album=album)
            import os
            if video.video:
                try:
                    if os.path.isfile(video.video.path):
                        os.remove(video.video.path)
                except Exception:
                    pass
            video.delete()
            dj_messages.success(request, 'Video deleted.')
            return redirect('booth_album_photos', pk=pk)

        elif action == 'bulk_delete':
            # Selecting forty photos and pressing Delete forty times was the only
            # way to empty an album. Photos and videos come from one set of
            # checkboxes, so a mixed selection is one action.
            photo_ids = [i for i in request.POST.getlist('photo_ids') if i.isdigit()]
            video_ids = [i for i in request.POST.getlist('video_ids') if i.isdigit()]
            gone_p = _remove_media(album.photos.filter(pk__in=photo_ids), 'image')
            gone_v = _remove_media(album.videos.filter(pk__in=video_ids), 'video')
            if gone_p:
                album.photo_count = album.photos.count()
                album.save(update_fields=['photo_count'])
            parts = []
            if gone_p:
                parts.append(f'{gone_p} photo{"s" if gone_p != 1 else ""}')
            if gone_v:
                parts.append(f'{gone_v} video{"s" if gone_v != 1 else ""}')
            if parts:
                dj_messages.success(request, f'{" and ".join(parts)} deleted.')
            else:
                dj_messages.info(request, 'Nothing was selected.')
            return redirect('booth_album_photos', pk=pk)

        elif action == 'set_cover':
            photo_id = request.POST.get('photo_id')
            photo = get_object_or_404(BoothPhoto, pk=photo_id, album=album)
            album.cover_photo = photo.image
            album.save(update_fields=['cover_photo'])
            dj_messages.success(request, 'Cover photo updated.')
            return redirect('booth_album_photos', pk=pk)

        elif action == 'clear_cover':
            album.cover_photo = None
            album.save(update_fields=['cover_photo'])
            dj_messages.success(request, 'Cover photo cleared.')
            return redirect('booth_album_photos', pk=pk)

    return render(request, 'portal/booth_album_photos.html', {
        'album':  album,
        'photos': photos,
        'videos': videos,
    })


# ── Policies & SOPs Management (Policies group only) ──────────────────────────

def _bound_page_title(path, fallback):
    """The CMS page title for a hardcoded route, or `fallback`.

    Cheap and forgiving on purpose: a missing page is a fresh install, not an
    error, and a section that cannot find its own name should still draw.
    """
    try:
        from cms.models import Page
        return (Page.objects.filter(bound_path=path)
                .values_list('title', flat=True).first() or fallback)
    except Exception:                                              # noqa: BLE001
        return fallback


def policies_group_required(view_func):
    """Gate the policy management screens.

    Shares user_can_manage_policies with the `is_policies_member` template flag,
    so the Manage buttons and the views behind them can never disagree — the
    previous pair of separate checks is how this ended up unreachable.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if user_can_manage_policies(request.user):
            return view_func(request, *args, **kwargs)
        raise Http404
    return wrapper


@policies_group_required
def policies_manage(request):
    """The old dashboard, now the way in to the screen that replaced it.

    Kept as a redirect rather than deleted: it is what everybody who manages a
    library has bookmarked, and it is where five save endpoints still send people
    when they are done. Landing on the library list is right in both cases.
    """
    return redirect('libraries_home')


def _forget_policy_file(policy):
    """Delete a document's file from disk, and never lose the record over it."""
    import os
    if not policy.file:
        return
    try:
        if os.path.isfile(policy.file.path):
            os.remove(policy.file.path)
    except Exception:
        pass


def _save_access(request, obj, scope):
    """Read the access controls off a posted form onto a library or document.

    Only touches them when the form actually carried an `access` field, so a
    save from some other form on the page cannot silently reset a library to
    "everyone" by simply not mentioning it.
    """
    from .models import DocAccess, INHERIT, LibraryAccess
    access = request.POST.get('access')
    if access is None:
        return
    allowed = {INHERIT, DocAccess.EVERYONE, DocAccess.RESTRICTED}
    if scope == 'library':
        allowed.discard(INHERIT)
        # Only a library can be listed-but-closed. A single document has no way
        # to be asked for on its own — the request is always for the library.
        allowed.add(LibraryAccess.REQUEST)
    obj.access = access if access in allowed else (
        INHERIT if scope == 'document' else DocAccess.EVERYONE)

    if scope == 'library':
        obj.downloadable = bool(request.POST.get('downloadable'))
    else:
        raw = (request.POST.get('downloadable') or '').strip()
        # '' is a third state here, not a missing value: it means "whatever the
        # library says", which is different from yes and from no.
        obj.downloadable = None if raw == '' else (raw == '1')
    obj.save()

    groups = [g for g in request.POST.getlist('allowed_groups') if g.isdigit()]
    users = [u for u in request.POST.getlist('allowed_users') if u.isdigit()]
    obj.allowed_groups.set(groups)
    obj.allowed_users.set(users)


@policies_group_required
def policy_category_save(request):
    """POST — add or update a category."""
    from .models import PolicyCategory
    if request.method == 'POST':
        pk    = request.POST.get('pk', '').strip()
        name  = request.POST.get('name', '').strip()
        desc  = request.POST.get('description', '').strip()
        icon  = request.POST.get('icon', '').strip()[:60]
        order = int(request.POST.get('order', 99) or 99)
        if name:
            if pk:
                category = PolicyCategory.objects.filter(pk=pk).first()
                # Somebody else's library is not editable from the shared list,
                # however the request was made.
                if category is None or not category.can_manage(request.user):
                    raise Http404
                PolicyCategory.objects.filter(pk=pk).update(
                    name=name, description=desc, icon=icon, order=order)
                _save_access(request, category, 'library')
                _tell(request, f'Category "{name}" updated.')
            else:
                # Whoever makes a library runs it: the settings are theirs, not
                # everyone's who happens to hold the same role.
                category = PolicyCategory.objects.create(
                    name=name, description=desc, icon=icon, order=order,
                    created_by=request.user if request.user.is_authenticated else None)
                _save_access(request, category, 'library')
                _tell(request, f'"{name}" created. You decide who can open it — '
                               f'open its settings to choose.')
    return redirect('policies_manage')


@policies_group_required
def policy_category_delete(request, pk):
    from .models import PolicyCategory
    cat = get_object_or_404(PolicyCategory, pk=pk)
    # Only whoever runs the library may remove it and everything filed in it.
    if not cat.can_manage(request.user):
        raise Http404
    if request.method == 'POST':
        name = cat.name
        # The documents go explicitly. `Policy.category` is SET_NULL, so this
        # said "and its documents deleted" while actually leaving every one of
        # them in the database with no library — which is where the 99 documents
        # behind the old "Other Documents" tile came from. libraries.library_remove
        # is the button people press now; this stays correct for anything still
        # posting here.
        for policy in cat.policies.all():
            _forget_policy_file(policy)
        cat.policies.all().delete()
        cat.delete()
        dj_messages.success(request, f'Category "{name}" and its documents deleted.')
    return redirect('policies_manage')


@policies_group_required
def policy_save(request, category_pk):
    """POST — add a new policy document to a category.

    Superseded by the workspace, which can also put the document in a folder —
    this one never could, so anything added through it landed at the top of the
    library whatever folder the person was looking at. Kept for anything still
    posting here, with the ownership check it was missing: being in the Policies
    group is not permission to add a document to somebody else's library.
    """
    from .models import PolicyCategory, Policy
    category = get_object_or_404(PolicyCategory, pk=category_pk)
    if not category.can_manage(request.user):
        raise Http404
    if request.method == 'POST':
        title       = request.POST.get('title', '').strip()
        description = request.POST.get('description', '').strip()
        external_url = request.POST.get('external_url', '').strip()
        order       = int(request.POST.get('order', 99) or 99)
        doc_type    = request.POST.get('doc_type', Policy.DocType.POLICY)
        if doc_type not in Policy.DocType.values:
            doc_type = Policy.DocType.POLICY
        if title:
            policy = Policy.objects.create(
                title=title, description=description, category=category,
                external_url=external_url, order=order, doc_type=doc_type,
            )
            if 'file' in request.FILES:
                policy.file = request.FILES['file']
                policy.save(update_fields=['file'])
            dj_messages.success(request, f'Document "{title}" added to {category.name}.')
    return redirect('policies_manage')


@policies_group_required
def policy_edit(request, pk):
    """POST — update an existing policy (including replacing the file)."""
    from .models import Policy
    policy = get_object_or_404(Policy, pk=pk)
    # Any member of the Policies group could edit any document in anybody's
    # library through this endpoint — the exact thing ownership exists to stop.
    if policy.category is not None and not policy.category.can_manage(request.user):
        raise Http404
    if request.method == 'POST':
        policy.title        = request.POST.get('title', policy.title).strip()
        policy.description  = request.POST.get('description', policy.description).strip()
        policy.external_url = request.POST.get('external_url', policy.external_url).strip()
        policy.order        = int(request.POST.get('order', policy.order) or policy.order)
        doc_type            = request.POST.get('doc_type', policy.doc_type)
        if doc_type in Policy.DocType.values:
            policy.doc_type = doc_type
        if 'file' in request.FILES:
            import os
            if policy.file:
                try:
                    if os.path.isfile(policy.file.path):
                        os.remove(policy.file.path)
                except Exception:
                    pass
            policy.file = request.FILES['file']
        policy.save()
        _save_access(request, policy, 'document')
        _tell(request, f'Document "{policy.title}" updated.')
    return redirect('policies_manage')


@policies_group_required
def policy_delete(request, pk):
    from .models import Policy
    import os
    policy = get_object_or_404(Policy, pk=pk)
    # Deleting somebody else's document, and its file, needed only membership of
    # the Policies group. Now it needs the library.
    if policy.category is not None and not policy.category.can_manage(request.user):
        raise Http404
    if request.method == 'POST':
        title = policy.title
        if policy.file:
            try:
                if os.path.isfile(policy.file.path):
                    os.remove(policy.file.path)
            except Exception:
                pass
        policy.delete()
        dj_messages.success(request, f'Document "{title}" deleted.')
    return redirect('policies_manage')


# ── HR Content Management (HR group only) ────────────────────────────────────

def hr_required(view_func):
    """Allow only members of the HR group (or superusers)."""
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if request.user.is_superuser or request.user.groups.filter(name='HR').exists():
            return view_func(request, *args, **kwargs)
        raise Http404
    return wrapper


def _content_manage(request, scope):
    """The content-management screen, showing only one page's content.

    One screen with two scopes rather than two near-identical templates. Which
    section belongs to which scope is decided by the page the content renders on,
    which is the point of the split: People & Culture content was reachable only
    from a URL under /onboarding/, so editing a wellness card meant going to the
    Onboarding module to do it.
    """
    from .models import OnboardingHandbookItem, WellnessProgram, ChildcarePartner, GrowthCertification, CEOLetter, EmployeeHandbookFile
    from content.models import WhatsAppChannel
    from .models import ExploreTab

    ob_tabs = OnboardingHandbookItem.TAB_CHOICES
    onboarding_sections = [
        (tab_id, tab_label, OnboardingHandbookItem.objects.filter(tab=tab_id))
        for tab_id, tab_label in ob_tabs
    ]

    ceo_letter, _    = CEOLetter.objects.get_or_create(pk=1)
    handbook_file, _ = EmployeeHandbookFile.objects.get_or_create(pk=1)

    return render(request, 'portal/hr_content_manage.html', {
        'scope': scope,
        'scope_title': ('People & Culture content' if scope == 'pc'
                        else 'Onboarding content'),
        'scope_back': '/people-culture/' if scope == 'pc' else '/onboarding/',
        'ob_tabs':             ob_tabs,
        'onboarding_sections': onboarding_sections,
        'wellness_items':      WellnessProgram.objects.all(),
        'childcare_partners':  ChildcarePartner.objects.all(),
        'certifications':      GrowthCertification.objects.all(),
        'ceo_letter':          ceo_letter,
        'handbook_file':       handbook_file,
        # WhatsApp channels were only editable in Django admin, which needs a
        # staff account. HR and Intranet Admins have neither, so nobody who
        # actually runs these groups could add a QR code or replace an expired one.
        'whatsapp_channels':   WhatsAppChannel.objects.all(),
        # Only this screen's own strip. Passing them all meant each screen
        # listed the other page's tabs as if they were its own.
        'explore_tabs':        ExploreTab.objects.filter(
            group=ExploreTab.Group.PC if scope == 'pc'
            else ExploreTab.Group.ONBOARDING),
    })


@hr_required
def hr_content_manage(request):
    """Onboarding content: the handbook tabs and the CEO letter.

    People & Culture content moved to /people-culture/manage/. Anything that
    still asks for it here is sent on rather than shown an empty screen, because
    saved links and bookmarks to this URL exist.
    """
    wants_pc = request.GET.get('section') in {
        'handbook', 'wellness', 'childcare', 'certifications', 'whatsapp', 'tabs'}
    if wants_pc:
        return redirect(f"/people-culture/manage/#{request.GET['section']}")
    return _content_manage(request, 'onboarding')


@hr_required
def people_culture_manage(request):
    """People & Culture content, reachable from the page it belongs to."""
    return _content_manage(request, 'pc')


@hr_required
def onboarding_item_save(request):
    """POST — add or update an onboarding item."""
    from .models import OnboardingHandbookItem
    if request.method == 'POST':
        pk    = request.POST.get('pk', '').strip()
        tab   = request.POST.get('tab', '').strip()
        title = request.POST.get('title', '').strip()
        body  = request.POST.get('body', '').strip()
        order = int(request.POST.get('order', 99) or 99)
        if tab and title:
            if pk:
                OnboardingHandbookItem.objects.filter(pk=pk).update(
                    tab=tab, title=title, body=body, order=order)
                dj_messages.success(request, f'"{title}" updated.')
            else:
                OnboardingHandbookItem.objects.create(tab=tab, title=title, body=body, order=order)
                dj_messages.success(request, f'"{title}" added.')
    return redirect(back_to('ob-items'))


@hr_required
def onboarding_item_delete(request, pk):
    from .models import OnboardingHandbookItem
    item = get_object_or_404(OnboardingHandbookItem, pk=pk)
    if request.method == 'POST':
        title = item.title
        item.delete()
        dj_messages.success(request, f'"{title}" deleted.')
    return redirect(back_to('ob-items'))


@hr_required
def whatsapp_channel_save(request):
    """POST — add or update one official WhatsApp channel, with its QR image.

    QR codes expire and get rotated, so replacing the image has to be something
    HR can do themselves rather than a job for whoever has a Django staff login.
    """
    from content.models import WhatsAppChannel
    if request.method != 'POST':
        return redirect(back_to('whatsapp'))

    pk = request.POST.get('pk', '').strip()
    title = request.POST.get('title', '').strip()
    if not title:
        dj_messages.error(request, 'A channel needs a name.')
        return redirect(back_to('whatsapp'))

    desc = request.POST.get('description', '').strip()[:300]
    join = request.POST.get('join_url', '').strip()
    try:
        order = int(request.POST.get('order') or 0)
    except (TypeError, ValueError):
        order = 0
    active = bool(request.POST.get('is_active'))

    channel = (WhatsAppChannel.objects.filter(pk=pk).first() if pk
               else None) or WhatsAppChannel()
    channel.title = title[:150]
    channel.description = desc
    channel.join_url = join
    channel.order = order
    channel.is_active = active
    upload = request.FILES.get('qr_image')
    if upload:
        channel.qr_image = upload
    channel.save()
    dj_messages.success(
        request, f'"{channel.title}" {"updated" if pk else "added"}.'
                 + ('' if channel.qr_image else ' Add its QR image next.'))
    return redirect(back_to('whatsapp'))


@hr_required
def whatsapp_channel_delete(request, pk):
    from content.models import WhatsAppChannel
    channel = get_object_or_404(WhatsAppChannel, pk=pk)
    if request.method == 'POST':
        title = channel.title
        # The image goes with the record rather than being orphaned on disk.
        if channel.qr_image:
            channel.qr_image.delete(save=False)
        channel.delete()
        dj_messages.success(request, f'"{title}" deleted.')
    return redirect(back_to('whatsapp'))


@hr_required
def explore_tab_save(request):
    """Rename an Explore tab, change its icon, reorder it or hide it.

    The key is deliberately not writable: it is what ties the tab to the panel it
    opens, so letting it be edited here would let somebody point a tab at nothing.
    """
    from .models import ExploreTab
    if request.method != 'POST':
        return redirect(back_to('tabs'))

    tab = ExploreTab.objects.filter(pk=request.POST.get('pk', '')).first()
    if tab is None:
        dj_messages.error(request, 'That tab no longer exists.')
        return redirect(back_to('tabs'))

    label = request.POST.get('label', '').strip()[:60]
    if not label:
        dj_messages.error(request, 'A tab needs a label.')
        return redirect(back_to('tabs'))

    tab.label = label
    tab.icon = request.POST.get('icon', '').strip()[:40]
    try:
        tab.order = max(0, int(request.POST.get('order') or 0))
    except (TypeError, ValueError):
        pass
    tab.is_active = bool(request.POST.get('is_active'))

    # Hiding every tab would leave the panel with nothing to open.
    if not tab.is_active and not ExploreTab.objects.filter(
            is_active=True).exclude(pk=tab.pk).exists():
        dj_messages.error(request, 'At least one tab has to stay visible.')
        return redirect(back_to('tabs'))

    tab.save()
    dj_messages.success(request, f'"{tab.label}" updated.')
    return redirect(back_to('tabs'))


@hr_required
def wellness_item_save(request):
    """POST — add or update a wellness program item."""
    from .models import WellnessProgram
    if request.method == 'POST':
        pk     = request.POST.get('pk', '').strip()
        name   = request.POST.get('name', '').strip()
        desc   = request.POST.get('description', '').strip()
        period = request.POST.get('period', '').strip()
        order  = int(request.POST.get('order', 99) or 99)
        icon   = request.POST.get('icon', '').strip()[:40]
        if name:
            if pk:
                WellnessProgram.objects.filter(pk=pk).update(
                    name=name, description=desc, period=period, order=order, icon=icon)
                dj_messages.success(request, f'"{name}" updated.')
            else:
                WellnessProgram.objects.create(name=name, description=desc,
                                               period=period, order=order, icon=icon)
                dj_messages.success(request, f'"{name}" added.')
    return redirect(back_to('wellness'))


@hr_required
def wellness_item_delete(request, pk):
    from .models import WellnessProgram
    item = get_object_or_404(WellnessProgram, pk=pk)
    if request.method == 'POST':
        name = item.name
        item.delete()
        dj_messages.success(request, f'"{name}" deleted.')
    return redirect(back_to('wellness'))


@hr_required
def childcare_partner_save(request):
    """POST — add a childcare partner."""
    from .models import ChildcarePartner
    if request.method == 'POST':
        name  = request.POST.get('name', '').strip()
        order = int(request.POST.get('order', 99) or 99)
        if name:
            ChildcarePartner.objects.create(name=name, order=order)
            dj_messages.success(request, f'"{name}" added.')
    return redirect(back_to('childcare'))


@hr_required
def childcare_partner_delete(request, pk):
    from .models import ChildcarePartner
    item = get_object_or_404(ChildcarePartner, pk=pk)
    if request.method == 'POST':
        name = item.name
        item.delete()
        dj_messages.success(request, f'"{name}" removed.')
    return redirect(back_to('childcare'))


@hr_required
def certification_save(request):
    """POST — add a certification, or update one that already exists.

    It only ever created before, ignoring any pk it was sent. Posting an edit
    would therefore have added a duplicate rather than changing the row, which is
    what setting an icon from this screen does.
    """
    from .models import GrowthCertification
    if request.method != 'POST':
        return redirect(back_to('certifications'))

    name = request.POST.get('name', '').strip()[:100]
    if not name:
        dj_messages.error(request, 'A certification needs a name.')
        return redirect(back_to('certifications'))

    try:
        order = int(request.POST.get('order') or 99)
    except (TypeError, ValueError):
        order = 99
    icon = request.POST.get('icon', '').strip()[:40]
    pk = (request.POST.get('pk') or '').strip()

    if pk:
        cert = GrowthCertification.objects.filter(pk=pk).first()
        if cert is None:
            dj_messages.error(request, 'That certification no longer exists.')
            return redirect(back_to('certifications'))
        cert.name, cert.order, cert.icon = name, order, icon
        cert.save(update_fields=['name', 'order', 'icon'])
        dj_messages.success(request, f'"{name}" updated.')
    else:
        if GrowthCertification.objects.filter(name__iexact=name).exists():
            dj_messages.error(request, f'"{name}" is already listed.')
        else:
            GrowthCertification.objects.create(name=name, order=order, icon=icon)
            dj_messages.success(request, f'"{name}" added.')
    return redirect(back_to('certifications'))


@hr_required
def certification_delete(request, pk):
    from .models import GrowthCertification
    item = get_object_or_404(GrowthCertification, pk=pk)
    if request.method == 'POST':
        name = item.name
        item.delete()
        dj_messages.success(request, f'"{name}" removed.')
    return redirect(back_to('certifications'))


# ── CEO Letter & Employee Handbook management (HR group only) ────────────────

@hr_required
def ceo_letter_save(request):
    """POST — update the CEO letter (name, title, body, sign-off, optional photo)."""
    from .models import CEOLetter
    letter, _ = CEOLetter.objects.get_or_create(pk=1)
    if request.method == 'POST':
        letter.name     = request.POST.get('name', letter.name).strip()
        letter.title    = request.POST.get('title', letter.title).strip()
        letter.body     = request.POST.get('body', letter.body)
        letter.sign_off = request.POST.get('sign_off', letter.sign_off).strip()
        if 'photo' in request.FILES:
            import os
            if letter.photo:
                try:
                    if os.path.isfile(letter.photo.path):
                        os.remove(letter.photo.path)
                except Exception:
                    pass
            letter.photo = request.FILES['photo']
        letter.save()
        dj_messages.success(request, 'CEO letter updated.')
    return redirect(back_to('ceo'))


@hr_required
def ceo_inline_save(request):
    """Save the CEO letter's name, title, sign-off or body, edited on the page.

    The letter is one of two halves of a box -- a picture and the words -- and the
    words were read-only because they live on a model rather than on the section,
    so the CMS inline editor had nothing to write to.

    A fixed whitelist, not a generic setter: `photo` must not be reachable from a
    text edit, or editing a line of prose becomes a way to replace the portrait.
    """
    from django.http import JsonResponse
    from .models import CEOLetter

    FIELDS = {'name': 120, 'title': 160, 'sign_off': 200, 'body': 20000}
    field = request.POST.get('field', '')
    if field not in FIELDS:
        return JsonResponse({'error': 'That field cannot be edited here.'}, status=400)

    value = (request.POST.get('value') or '').strip()[:FIELDS[field]]
    letter, _ = CEOLetter.objects.get_or_create(pk=1)
    if field in ('name', 'body') and not value:
        return JsonResponse({'error': 'That cannot be left empty.'}, status=400)

    was = getattr(letter, field)
    setattr(letter, field, value)
    letter.save(update_fields=[field])
    return JsonResponse({'ok': True, 'message': 'Saved.',
                         'undo': {'kind': 'ceo', 'field': field, 'value': was}})


@hr_required
def certification_inline(request):
    """Add, rename or remove a recommended certification from the page itself.

    The list was already records with a management screen, but reaching it meant
    leaving the page you were looking at. Answers JSON so the chip updates in
    place instead of reloading.
    """
    from django.http import JsonResponse
    from .models import GrowthCertification

    if request.method != 'POST':
        return JsonResponse({'error': 'Use POST.'}, status=405)

    action = request.POST.get('action', '')
    name = (request.POST.get('name') or '').strip()[:100]
    pk = (request.POST.get('pk') or '').strip()

    if action == 'add':
        if not name:
            return JsonResponse({'error': 'Type a name for the certification.'},
                                status=400)
        if GrowthCertification.objects.filter(name__iexact=name).exists():
            return JsonResponse({'error': f'"{name}" is already listed.'}, status=400)
        last = GrowthCertification.objects.order_by('-order').first()
        cert = GrowthCertification.objects.create(
            name=name, order=(last.order + 1) if last else 0)
        return JsonResponse({'ok': True, 'message': f'"{name}" added.',
                             'pk': cert.pk, 'name': cert.name})

    if action == 'rename':
        cert = GrowthCertification.objects.filter(pk=pk).first()
        if cert is None:
            return JsonResponse({'error': 'That certification no longer exists.'},
                                status=404)
        if not name:
            return JsonResponse({'error': 'A certification needs a name.'}, status=400)
        clash = GrowthCertification.objects.filter(name__iexact=name).exclude(pk=cert.pk)
        if clash.exists():
            return JsonResponse({'error': f'"{name}" is already listed.'}, status=400)
        was = cert.name
        cert.name = name
        cert.save(update_fields=['name'])
        return JsonResponse({'ok': True, 'message': 'Saved.',
                             'undo': {'kind': 'cert', 'pk': cert.pk, 'name': was}})

    if action == 'remove':
        cert = GrowthCertification.objects.filter(pk=pk).first()
        if cert is None:
            return JsonResponse({'error': 'That certification no longer exists.'},
                                status=404)
        was = cert.name
        cert.delete()
        return JsonResponse({'ok': True, 'message': f'"{was}" removed.'})

    return JsonResponse({'error': 'Unknown action.'}, status=400)


@hr_required
def handbook_inline_save(request):
    """Save the handbook's heading, eyebrow or description edited on the page.

    The handbook was the clearest example of a block that looked editable and was
    not: its text lives on a model rather than on the section, so the CMS inline
    editor had nothing to write to and every field was read-only.

    Deliberately a fixed whitelist rather than a generic "set any field" endpoint:
    the same request must not be able to reach the file, or a text edit becomes a
    way to swap the document.
    """
    from django.http import JsonResponse
    from .models import EmployeeHandbookFile

    FIELDS = {
        'title':       200,
        'description': 4000,
        'eyebrow':     60,
    }
    field = request.POST.get('field', '')
    if field not in FIELDS:
        return JsonResponse({'error': 'That field cannot be edited here.'}, status=400)

    value = (request.POST.get('value') or '').strip()[:FIELDS[field]]
    hb, _ = EmployeeHandbookFile.objects.get_or_create(pk=1)

    if field == 'title' and not value:
        return JsonResponse({'error': 'The handbook needs a title.'}, status=400)

    if field == 'eyebrow':
        # Not a column on the model; it is presentation, so it rides in the
        # section's own config where the rest of the block's wording lives.
        from cms.models import Section
        block = Section.all_objects.filter(type='feature',
                                           config__widget='pc_work').first()
        if block is None:
            return JsonResponse({'error': 'That block no longer exists.'}, status=404)
        cfg = dict(block.config or {})
        cfg['eyebrow'] = value
        block.config = cfg
        block.save(update_fields=['config'])
        return JsonResponse({'ok': True, 'message': 'Saved.'})

    was = getattr(hb, field)
    setattr(hb, field, value)
    hb.save(update_fields=[field])
    # Reported back so the editor's undo can put it there again.
    return JsonResponse({'ok': True, 'message': 'Saved.',
                         'undo': {'kind': 'handbook', 'field': field, 'value': was}})


@hr_required
def handbook_file_save(request):
    """POST — update the Employee Handbook title/description and replace the file."""
    from .models import EmployeeHandbookFile
    hb, _ = EmployeeHandbookFile.objects.get_or_create(pk=1)
    if request.method == 'POST':
        hb.title       = request.POST.get('title', hb.title).strip()
        hb.description = request.POST.get('description', hb.description).strip()
        if 'file' in request.FILES:
            import os
            if hb.file:
                try:
                    if os.path.isfile(hb.file.path):
                        os.remove(hb.file.path)
                except Exception:
                    pass
            hb.file = request.FILES['file']
        hb.save()
        dj_messages.success(request, 'Employee Handbook updated.')
    return redirect(back_to('handbook'))


# ── Controlled documents ─────────────────────────────────────────────────────
#
# The three endpoints the viewer talks to. Each one asks the same question
# before doing anything, because each is reachable on its own: hiding a link
# protects nothing when the address is guessable, which is the state /media/ is
# in today.

def _policy_for(request, pk):
    """A policy this reader is allowed to open, or 404.

    404 rather than 403 on purpose. Telling somebody "this exists but is not for
    you" leaks the existence of a document and often its subject with it, and
    the restricted libraries here are the ones where that matters.
    """
    policy = get_object_or_404(
        Policy.objects.select_related('category')
        .prefetch_related('allowed_groups', 'allowed_users',
                          'category__allowed_groups', 'category__allowed_users'),
        pk=pk)
    if not policy.allows(request.user):
        raise Http404
    if not policy.file:
        raise Http404
    return policy


# ── Every other document in the Hub ──────────────────────────────────────────
#
# The handbook and the brand files want the same viewer and the same guarded
# serving; what differs is only how each one is found and who may have it. A
# source is those three answers, so there is one set of views rather than a
# near-copy of them per model — which is the shape that drifts.
#
# Neither of these carries the libraries' group-and-person rules. They are
# company-wide documents, so the check is "are you signed in", which is still
# more than /media/ asks.

def _resolve_handbook(request, pk):
    from .models import EmployeeHandbookFile
    handbook = get_object_or_404(EmployeeHandbookFile, pk=pk)
    if not handbook.file:
        raise Http404
    return handbook.file, handbook.title or 'Employee handbook', 'Onboarding', True


def _resolve_brandfile(request, pk):
    from .models import BrandTemplateFile
    entry = get_object_or_404(
        BrandTemplateFile.objects.select_related('template'), pk=pk)
    if not entry.file:
        raise Http404
    template = entry.template.name if entry.template else 'Brand files'
    return entry.file, entry.label or template, template, True


def _resolve_dam(request, pk):
    """A DAM asset, subject to the library's own folder permissions.

    DAM already decides who may see what, per folder and up the whole ancestor
    chain — `Folder.is_visible_to`. That is reused rather than replaced: two
    systems answering "may this person see this file" is one too many, and the
    one that would drift is whichever is not the app's own.
    """
    from dam.models import Asset
    asset = get_object_or_404(
        Asset.objects.select_related('folder'), pk=pk, archived=False)
    if not asset.file:
        raise Http404
    folder = asset.folder
    if folder is not None and not folder.is_visible_to(request.user):
        raise Http404
    return (asset.file, asset.name or 'Asset',
            folder.name if folder is not None else 'Library', True)


def _resolve_exchange(request, pk):
    """An attachment posted to a community, subject to that community's own
    read permission.

    Same reasoning as the DAM resolver above: Exchange already decides who may
    see inside a community, so `Community.can_read` is reused rather than
    reimplemented here. Anything posted to a community may be saved by anyone
    who can read it — a file somebody attached to a thread is not a controlled
    document, and pretending otherwise would only hide the Save button.
    """
    from exchange.models import Attachment
    attachment = get_object_or_404(
        Attachment.objects.select_related('community', 'thread'), pk=pk)
    if not attachment.file:
        raise Http404
    if not attachment.community.can_read(request.user):
        raise Http404
    subtitle = (attachment.thread.title if attachment.thread_id
                else attachment.community.name)
    return attachment.file, attachment.name or 'Attachment', subtitle, True


#: source slug -> a function returning (file_field, title, subtitle, may_download)
DOC_SOURCES = {
    'handbook': _resolve_handbook,
    'brandfile': _resolve_brandfile,
    'dam': _resolve_dam,
    'exchange': _resolve_exchange,
}


def _resolve(request, source, pk):
    resolver = DOC_SOURCES.get(source)
    if resolver is None:
        raise Http404
    return resolver(request, pk)


@login_required
def document_meta(request, source, pk):
    """What the viewer needs to open a handbook or brand file."""
    from django.urls import reverse
    from . import documents
    file_field, title, subtitle, may_download = _resolve(request, source, pk)
    page_url = reverse('document_page', args=[source, pk, 999999]).replace('999999', '{n}')
    return documents.viewer_payload(
        request,
        doc_url=page_url,
        title=title,
        subtitle=subtitle,
        file_field=file_field,
        may_download=may_download,
        download_url=reverse('document_file', args=[source, pk]) if may_download else '',
    )


@login_required
def document_page(request, source, pk, index):
    from . import documents
    file_field, _title, _sub, _dl = _resolve(request, source, pk)
    return documents.page_response(file_field, index)


@login_required
def document_file(request, source, pk):
    from . import documents
    file_field, _title, _sub, may_download = _resolve(request, source, pk)
    if not may_download:
        raise Http404
    return documents.file_response(file_field, download=True)


@login_required
def policy_document(request, pk):
    """What the viewer needs to open this document."""
    from . import documents
    from django.urls import reverse
    policy = _policy_for(request, pk)
    may_download = policy.may_download(request.user)
    # Reversed with a page number that cannot occur and then swapped for the
    # placeholder, so the viewer builds page URLs without this file having to
    # know the shape of the route.
    page_url = reverse('policy_doc_page', args=[policy.pk, 999999]).replace('999999', '{n}')
    return documents.viewer_payload(
        request,
        doc_url=page_url,
        title=policy.title,
        subtitle=policy.category.name if policy.category else '',
        file_field=policy.file,
        may_download=may_download,
        download_url=reverse('policy_doc_file', args=[policy.pk]) if may_download else '',
    )



@login_required
def policy_doc_page(request, pk, index):
    """One rendered page. The source file never leaves the server."""
    from . import documents
    policy = _policy_for(request, pk)
    return documents.page_response(policy.file, index)


@login_required
def policy_doc_file(request, pk):
    """The document itself — only for those allowed to keep a copy."""
    from . import documents
    policy = _policy_for(request, pk)
    if not policy.may_download(request.user):
        raise Http404
    return documents.file_response(policy.file, download=True)


# ── One library's own settings ───────────────────────────────────────────────
#
# A library is often for people from several divisions who have nothing else in
# common, so its settings belong to whoever set it up — not to everyone holding
# the same role. Seeing a library and running it are separate permissions.

def _library_for_manage(request, pk):
    from .models import PolicyCategory
    category = get_object_or_404(
        PolicyCategory.objects.prefetch_related('allowed_groups', 'allowed_users'), pk=pk)
    if not category.can_manage(request.user):
        raise Http404
    return category


@policies_group_required
def policy_library_settings(request, pk):
    """The old standalone settings page — now a tab of the workspace.

    Every one of its fields, its deputies picker and its request queue moved
    there, next to the documents they govern. What is left is the redirect,
    because three other views still send people here when they are done and a
    bookmark should land somewhere that works rather than 404.
    """
    return redirect(reverse('library_workspace', args=[pk]) + '?tab=settings')


@policies_group_required
def policy_people_search(request):
    """Colleagues, for the deputies picker on a library's settings.

    Its own endpoint rather than Brand Files' one, which is gated on the
    Branding group — a policy owner is not necessarily in it, and borrowing it
    would have meant the picker silently returned nothing for most of the people
    who need it.
    """
    from core.people import search_people
    return JsonResponse({'users': search_people(request.GET.get('q', ''))})


@login_required
@require_POST
def policy_access_request(request, pk):
    """A reader asking to be let into a request-based library.

    One row per person, reused: asking again after being declined re-opens the
    same row rather than queueing a second copy in front of the owner.
    """
    from .models import LibraryAccess, PolicyAccessRequest, PolicyCategory
    library = get_object_or_404(PolicyCategory, pk=pk)
    # Only a listed-but-closed library can be asked for. Asking about a library
    # that is already open, or one they cannot see at all, is not a thing that
    # can happen from the interface — so it is a 404 rather than a message.
    if library.access != LibraryAccess.REQUEST or library.allows(request.user):
        raise Http404

    row, _ = PolicyAccessRequest.objects.get_or_create(
        library=library, user=request.user)
    row.message = (request.POST.get('message') or '').strip()[:300]
    row.status = PolicyAccessRequest.Status.PENDING
    row.resolved_at = None
    row.resolved_by = None
    row.save()

    who = request.user.get_full_name() or request.user.username
    from django.contrib.auth.models import User as _User
    from core.models import NotificationEvent as _Event
    from core.notify import notify_many
    notify_many(_User.objects.filter(pk__in=library.manager_ids()),
                _Event.GENERAL,
                title=f'{who} asked for access to {library.name}',
                body=row.message or 'No reason given.',
                # Straight to the queue, not to the settings the queue used to
                # be buried in.
                link=(reverse('library_workspace', args=[library.pk])
                      + '?tab=requests'),
                actor=request.user)
    _tell(request, f'Asked for access to "{library.name}". '
                   f'You will hear when it is answered.')
    return redirect('policies')


def _folder_for_manage(request, pk):
    """One folder, if this person runs the library it belongs to."""
    from .models import PolicyFolder
    folder = get_object_or_404(
        PolicyFolder.objects.select_related('library', 'parent'), pk=pk)
    if not folder.library.can_manage(request.user):
        raise Http404
    return folder


@policies_group_required
@require_POST
def policy_folder_add(request, pk):
    """A new folder in a library, or inside another folder."""
    from .models import PolicyFolder
    library = _library_for_manage(request, pk)
    name = (request.POST.get('name') or '').strip()[:200]
    if not name:
        _tell(request, 'A folder needs a name.', 'error')
        return _back_to_folder(request, library)

    parent = None
    raw = (request.POST.get('parent') or '').strip()
    if raw.isdigit():
        parent = PolicyFolder.objects.filter(pk=int(raw), library=library).first()
    # The depth cap is the model's, checked here so the message names it rather
    # than the folder quietly landing at the top of the library instead.
    if parent is not None and parent.depth + 1 > PolicyFolder.MAX_DEPTH:
        _tell(request, f'Folders only go {PolicyFolder.MAX_DEPTH} deep.', 'error')
        return _back_to_folder(request, library, parent)

    PolicyFolder.objects.create(library=library, parent=parent, name=name,
                                created_by=request.user)
    _tell(request, f'Folder "{name}" added.')
    return _back_to_folder(request, library, parent)


@policies_group_required
@require_POST
def policy_folder_rename(request, pk):
    folder = _folder_for_manage(request, pk)
    name = (request.POST.get('name') or '').strip()[:200]
    if name:
        folder.name = name
        folder.save(update_fields=['name'])
        _tell(request, 'Folder renamed.')
    return _back_to_folder(request, folder.library, folder.parent)


@policies_group_required
@require_POST
def policy_folder_move(request, pk):
    """Re-file a folder under another one, or back to the top of the library."""
    from .models import PolicyFolder
    folder = _folder_for_manage(request, pk)
    raw = (request.POST.get('parent') or '').strip()
    target = None
    if raw.isdigit():
        target = PolicyFolder.objects.filter(pk=int(raw),
                                             library=folder.library).first()
    # may_move_to refuses the three impossible moves: into itself, into its own
    # descendant — which would cut that branch off the tree and leave it
    # unreachable — and past the depth cap.
    if not folder.may_move_to(target):
        _tell(request, 'A folder cannot be moved inside itself, and only '
                       f'{PolicyFolder.MAX_DEPTH} levels are allowed.', 'error')
        return _back_to_folder(request, folder.library, folder.parent)
    folder.parent = target
    folder.save(update_fields=['parent'])
    _tell(request, f'Moved to {target.full_path if target else folder.library.name}.')
    return _back_to_folder(request, folder.library, target)


@policies_group_required
@require_POST
def policy_folder_delete(request, pk):
    """Remove a folder and keep everything that was in it.

    Deliberately not a cascade. The foreign keys would take every subfolder with
    it and cut its documents loose to the top of the library, so deleting a
    folder somebody had filed three levels of manuals under would quietly
    scatter them. Children and documents move up to where the folder was, and
    only the folder itself goes.
    """
    folder = _folder_for_manage(request, pk)
    parent, library = folder.parent, folder.library
    moved_docs = folder.documents.update(folder=parent)
    moved_subs = folder.children.update(parent=parent)
    name = folder.name
    folder.delete()
    where = parent.name if parent else library.name
    extra = ''
    if moved_docs or moved_subs:
        bits = []
        if moved_docs:
            bits.append(f'{moved_docs} document{"" if moved_docs == 1 else "s"}')
        if moved_subs:
            bits.append(f'{moved_subs} folder{"" if moved_subs == 1 else "s"}')
        extra = f' {" and ".join(bits)} moved up to {where}.'
    _tell(request, f'Folder "{name}" removed.{extra}')
    return _back_to_folder(request, library, parent)


@policies_group_required
@require_POST
def policy_files_upload(request, pk):
    """Several files at once, straight into the folder they are looked at in.

    One document per file, titled from the filename. Adding a manual should be
    dropping the file where it belongs, not filling in a form per PDF — and the
    title, the type and the dates can all be corrected afterwards from the
    library's settings.
    """
    from .models import Policy, PolicyFolder
    library = _library_for_manage(request, pk)
    parent = None
    raw = (request.POST.get('folder') or '').strip()
    if raw.isdigit():
        parent = PolicyFolder.objects.filter(pk=int(raw), library=library).first()

    files = request.FILES.getlist('files')
    if not files:
        _tell(request, 'No files were chosen.', 'error')
        return _back_to_folder(request, library, parent)

    doc_type = request.POST.get('doc_type') or Policy.DocType.POLICY
    if doc_type not in dict(Policy.DocType.choices):
        doc_type = Policy.DocType.POLICY

    made = 0
    for f in files:
        # The filename without its extension. A document called
        # "QHSE-Manual-2026.pdf" should be listed as "QHSE-Manual-2026", not
        # with the extension the icon already tells you about.
        stem = (f.name or 'Document').rsplit('/', 1)[-1]
        title = stem.rsplit('.', 1)[0] if '.' in stem else stem
        Policy.objects.create(category=library, folder=parent,
                              title=title[:300] or 'Document',
                              doc_type=doc_type, file=f)
        made += 1
    _tell(request, f'{made} file{"" if made == 1 else "s"} added'
                   f'{" to " + parent.name if parent else ""}.')
    return _back_to_folder(request, library, parent)


def _back_to_folder(request, library, folder=None):
    """Back to the workspace, in the folder that was being worked in.

    These endpoints are only reachable by somebody who runs the library, so the
    place to return to is the screen they run it from — not the reader's page,
    which no longer carries any of these forms.
    """
    url = reverse('library_workspace', args=[library.pk])
    if folder is not None:
        url += f'?folder={folder.pk}'
    return redirect(url)


@policies_group_required
@require_POST
def policy_request_decide(request, pk):
    """The owner or a deputy answering one request.

    Approving adds the person to the library's allowed_users, which is what
    actually lets them in — the request row is only the conversation about it.
    That way there is one gate, and revoking access later is done in the same
    place as granting it to anybody else.
    """
    from django.utils import timezone
    from .models import PolicyAccessRequest
    row = get_object_or_404(
        PolicyAccessRequest.objects.select_related('library', 'user'), pk=pk)
    if not row.library.can_manage(request.user):
        raise Http404

    approve = request.POST.get('decision') == 'approve'
    row.status = (PolicyAccessRequest.Status.APPROVED if approve
                  else PolicyAccessRequest.Status.DECLINED)
    row.resolved_at = timezone.now()
    row.resolved_by = request.user
    row.save()
    if approve:
        row.library.allowed_users.add(row.user)

    from core.models import NotificationEvent as _Event
    from core.notify import notify
    notify(row.user, _Event.GENERAL,
           title=('You can now open %s' % row.library.name) if approve
                 else ('Your request for %s was declined' % row.library.name),
           body='' if approve else 'Ask the library owner if you need it.',
           link=reverse('policies') + f'?cat={row.library.pk}' if approve
                else reverse('policies'),
           actor=request.user)
    name = row.user.get_full_name() or row.user.username
    _tell(request, f'{name} {"can now open" if approve else "was declined for"} '
                   f'"{row.library.name}".')
    return redirect('policy_library_settings', pk=row.library.pk)


@policies_group_required
def policy_library_claim(request, pk):
    """Take ownership of a library that predates ownership."""
    category = _library_for_manage(request, pk)
    if request.method == 'POST' and not category.created_by_id:
        category.created_by = request.user
        category.save(update_fields=['created_by'])
        _tell(request, f'"{category.name}" is yours now — only you can change its settings.')
    return redirect('policy_library_settings', pk=pk)


@policies_group_required
def policy_access_save(request, pk):
    """One document's access override, from its library's settings page."""
    from .models import Policy
    policy = get_object_or_404(Policy.objects.select_related('category'), pk=pk)
    # Checked against the library, not the document: whoever runs the library
    # decides what the documents in it may do.
    if policy.category is None or not policy.category.can_manage(request.user):
        raise Http404
    if request.method == 'POST':
        _save_access(request, policy, 'document')
        _tell(request, f'Access for "{policy.title}" updated.')
    return redirect('policy_library_settings', pk=policy.category.pk)
