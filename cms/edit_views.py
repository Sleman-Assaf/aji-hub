# cms/edit_views.py
"""Endpoints for the on-page editor.

Every one of these returns JSON and mutates a single block, so the editor never
reloads the page it is editing. They are deliberately separate from views.py,
which serves the form-based management screens — those remain, because a long
form is still the better way to edit a table or a block of HTML.

Permission is checked on every call: the toolbar being hidden is a courtesy, the
gate is here.
"""
import json
from functools import wraps

from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from . import palette, providers, widgets
from .models import (Audience, Layout, Page, Section, SectionItem, SectionType,
                     layout_options, user_can_edit_cms)
from .widgets import widget_choices

#: Fields the inline editor may write directly from contenteditable text.
INLINE_FIELDS = {'title', 'subtitle', 'body'}

#: Fields the settings drawer may write, with the type to coerce them to.
SETTINGS_FIELDS = {
    'title': str, 'subtitle': str, 'body': str, 'icon': str,
    'audience': str, 'layout': str, 'equal_height': bool, 'is_visible': bool,
    # An id from the media library, or empty to clear it.
    'image_asset': 'asset',
}

#: config keys the drawer may write. Anything else is ignored rather than stored,
#: so a stray field cannot become a permanent part of a block's settings.
CONFIG_KEYS = {'limit', 'link_url', 'link_label', 'columns', 'clock_label', 'eyebrow_url',
               'hide_clock', 'hide_icons', 'eyebrow', 'widget'}


def editor_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not user_can_edit_cms(request.user):
            return JsonResponse(
                {'error': "You don't have permission to edit this page."}, status=403)
        return view(request, *args, **kwargs)
    return wrapper


def _payload(request):
    if request.content_type and 'json' in request.content_type:
        try:
            return json.loads(request.body or '{}')
        except ValueError:
            return {}
    return request.POST


def _section(pk):
    # all_objects, so restoring a removed block can still find it.
    return get_object_or_404(Section.all_objects.select_related('page', 'parent'), pk=pk)


def _render_block(request, section):
    """Re-render one block so the client can swap it in place."""
    from .templatetags.cms_tags import cms_block
    return cms_block({'request': request, 'cms_edit_mode': True}, section)


def _block_json(request, section, message=''):
    return JsonResponse({
        'ok': True,
        'message': message,
        'pk': section.pk,
        'html': _render_block(request, section),
    })


# ── inline text ───────────────────────────────────────────────────────────
@require_POST
@editor_required
def inline_save(request, pk):
    """Save one field edited directly on the page."""
    section = _section(pk)
    data = _payload(request)
    field = data.get('field')
    value = (data.get('value') or '').strip()

    # txt_<key> writes one of the words inside a content panel — a heading or a
    # description that used to be typed into the feature's template, where no
    # editor could reach it. The widget declares which keys exist, so this can
    # only ever overwrite a string the panel already renders.
    if field.startswith('txt_') if field else False:
        from .widgets import labels_for, widget_key
        key = field[4:]
        wkey = widget_key(section)
        if key not in labels_for(wkey):
            return JsonResponse(
                {'error': f'“{key}” is not one of this panel’s editable labels.'},
                status=400)
        cfg = dict(section.config or {})
        text = dict(cfg.get('text') or {})
        # What was on screen, which is the panel's default until somebody has
        # overridden it. Handing back the stored value would send an empty string
        # the first time a line is changed, so undoing that first edit would
        # blank the heading rather than put it back.
        was = text.get(key)
        if was is None:
            was = labels_for(wkey)[key][0]
        if data.get('rich'):
            # A list or a run of paragraphs. Reduced to the allow-list here,
            # because feature_rich renders it without escaping — the panel needs
            # its bullets to stay bullets.
            from .richtext import sanitise_html
            text[key] = sanitise_html(data.get('value') or '')[:4000]
        else:
            text[key] = value[:400]
        cfg['text'] = text
        section.config = cfg
        section.save(update_fields=['config'])
        return JsonResponse({'ok': True, 'message': 'Saved.',
                             'undo': {'kind': 'inline', 'pk': section.pk,
                                      'field': field, 'value': was}})

    # cfg_<key> writes a config value, which is how a heading's eyebrow is
    # editable in place without giving every setting its own column.
    if field.startswith('cfg_') if field else False:
        key = field[4:]
        if key not in CONFIG_KEYS:
            return JsonResponse({'error': f'Setting "{key}" is not editable inline.'},
                                status=400)
        cfg = dict(section.config or {})
        cfg[key] = value[:300]
        section.config = cfg
        section.save(update_fields=['config'])
        return JsonResponse({'ok': True, 'message': 'Saved.'})

    if field not in INLINE_FIELDS:
        return JsonResponse({'error': f'Field "{field}" is not editable inline.'},
                            status=400)
    if field in ('title', 'subtitle'):
        # Headings stay plain text: markup in a heading fights the type scale and
        # there is nothing useful to say with it.
        value = value[:400]
    elif field == 'body' and data.get('rich'):
        # A body may carry formatting. It is reduced to the allow-list before it
        # is stored, so what reaches the database is already safe to render.
        from .richtext import sanitise_html
        value = sanitise_html(data.get('value') or '')
    was = getattr(section, field, '')
    setattr(section, field, value)
    section.save(update_fields=[field])
    # The previous value travels back so an undo needs no extra request to
    # discover what it should restore.
    return JsonResponse({'ok': True, 'message': 'Saved.',
                         'undo': {'kind': 'inline', 'pk': section.pk,
                                  'field': field, 'value': was}})


@require_POST
@editor_required
def item_inline_save(request, pk):
    """Same, for a row inside a block — a tile's label, for instance."""
    item = get_object_or_404(SectionItem, pk=pk)
    data = _payload(request)
    field = data.get('field')
    # icon and is_visible joined the list when the drawer stopped sending
    # people to the full section form to set them.
    if field not in {'title', 'text', 'meta', 'badge', 'url', 'button_label',
                     'icon', 'is_visible'}:
        return JsonResponse({'error': f'Field "{field}" is not editable inline.'},
                            status=400)
    raw = (data.get('value') or '').strip()[:500]
    was = getattr(item, field, '')
    value = raw.lower() in ('1', 'true', 'on', 'yes') if field == 'is_visible' else raw
    setattr(item, field, value)
    item.save(update_fields=[field])
    # The owning block comes back redrawn, so a change made in the drawer shows
    # on the page at once rather than at the next load.
    return JsonResponse({'ok': True, 'message': 'Saved.',
                         'block': item.section_id,
                         'html': _render_block(request, item.section),
                         'undo': {'kind': 'item_inline', 'pk': item.pk,
                                  'field': field, 'value': was}})


# ── block lifecycle ───────────────────────────────────────────────────────
@editor_required
def page_start(request):
    """Create a page and go straight into editing it.

    The old route was: fill in a settings form, save, land on a management screen,
    click Add section, fill in another form, save, go back. Seven steps before a
    word of content. This is one: the page exists, it has a heading you can type
    over, and the block palette is one click away — the same as starting a page in
    SharePoint or Notion.

    It starts as a draft, so nothing is visible to anyone else until it is
    published. Nothing is lost if the tab is closed: it is a real page from the
    first moment, listed under Hub pages.
    """
    from django.utils.text import slugify

    base = slugify(request.GET.get('title') or 'untitled-page') or 'untitled-page'
    slug, n = base, 2
    while Page.objects.filter(slug=slug).exists():
        slug = f'{base}-{n}'
        n += 1

    page = Page.objects.create(
        title=request.GET.get('title') or 'Untitled page',
        slug=slug, is_published=False, updated_by=request.user,
        subtitle='')

    # A heading block so there is something to type into immediately rather than
    # an empty screen with a single button on it.
    Section.objects.create(page=page, type=SectionType.PAGE_HEAD, order=10,
                           title=page.title,
                           config={'eyebrow': ''})

    return redirect(f'/p/{page.slug}/?edit=1&new=1')


@require_POST
@editor_required
def block_create(request, slug):
    """Add a block, optionally into a column of an existing row."""
    page = get_object_or_404(Page, slug=slug)
    data = _payload(request)

    stype = data.get('type')
    if stype not in dict(SectionType.choices):
        return JsonResponse({'error': 'Unknown block type.'}, status=400)

    parent = None
    parent_pk = data.get('parent') or None
    if parent_pk:
        parent = get_object_or_404(Section, pk=parent_pk, page=page)
        if not parent.is_row:
            return JsonResponse({'error': 'That block cannot hold other blocks.'},
                                status=400)
        # A row inside a row would render but could not be laid out sensibly on
        # a narrow screen, where every row collapses to one column.
        if stype == SectionType.ROW:
            return JsonResponse({'error': 'A columns row cannot be nested inside '
                                          'another columns row.'}, status=400)

    try:
        column = max(0, int(data.get('column') or 0))
    except (TypeError, ValueError):
        column = 0
    if parent is not None:
        column = min(column, parent.column_count - 1)

    siblings = Section.objects.filter(page=page, parent=parent)
    if parent is not None:
        siblings = siblings.filter(column=column)

    # Where it lands. `before` is the block it should sit above; without one it
    # goes last, which is all this used to do — so every insert appeared at the
    # bottom of the page and had to be dragged back up to where it was wanted.
    order = None
    before_pk = data.get('before') or None
    if before_pk:
        anchor = siblings.filter(pk=before_pk).order_by('order').first()
        if anchor is not None:
            above = (siblings.filter(order__lt=anchor.order)
                     .order_by('-order').first())
            if above is None:
                order = anchor.order - 5
            elif anchor.order - above.order > 1:
                order = (anchor.order + above.order) // 2
            else:
                # No gap to slot into. Spread this run out and try again, rather
                # than silently putting the block somewhere else.
                for i, sib in enumerate(siblings.order_by('order', 'pk'), start=1):
                    if sib.order != i * 10:
                        Section.objects.filter(pk=sib.pk).update(order=i * 10)
                anchor.refresh_from_db()
                above = (siblings.filter(order__lt=anchor.order)
                         .order_by('-order').first())
                order = ((anchor.order + above.order) // 2 if above
                         else anchor.order - 5)
    if order is None:
        last = siblings.order_by('-order').first()
        order = (last.order + 10) if last else 10

    config = {}
    if stype == SectionType.FEATURE:
        from .widgets import WIDGETS
        widget = data.get('widget') or ''
        if widget not in WIDGETS:
            return JsonResponse({'error': 'Choose which page feature to place.'},
                                status=400)
        config['widget'] = widget

    section = Section.objects.create(
        page=page, parent=parent, column=column, type=stype,
        order=order,
        layout=Layout.HALF if stype == SectionType.ROW else Layout.FULL,
        title=data.get('title', '') or '',
        config=config,
    )
    return JsonResponse({
        'ok': True,
        'message': f'{section.get_type_display()} added.',
        'pk': section.pk,
        'parent': parent.pk if parent else None,
        'column': column,
        'html': _render_block(request, section),
    })


@require_POST
@editor_required
def block_update(request, pk):
    """Settings-drawer save. Only whitelisted fields and config keys apply."""
    section = _section(pk)
    data = _payload(request)

    changed = []
    for field, caster in SETTINGS_FIELDS.items():
        if field not in data:
            continue
        raw = data.get(field)
        if caster is bool:
            value = str(raw).lower() in ('1', 'true', 'yes', 'on')
        elif caster == 'asset':
            # A media-library reference. An unknown id clears the field rather
            # than pointing a page at something that does not exist.
            from dam.models import Asset
            field = 'image_asset_id'
            try:
                value = Asset.objects.filter(pk=int(raw)).values_list('pk', flat=True).first()
            except (TypeError, ValueError):
                value = None
        else:
            value = (raw or '').strip()
        if field == 'layout' and value not in dict(Layout.choices):
            continue
        setattr(section, field, value)
        changed.append(field)

    cfg = dict(section.config or {})
    incoming = data.get('config') or {}
    if isinstance(incoming, str):
        try:
            incoming = json.loads(incoming)
        except ValueError:
            incoming = {}
    for key, value in (incoming or {}).items():
        if key in CONFIG_KEYS:
            cfg[key] = value

    # txt_<key>: a content panel's own wording, saved from the drawer as well as
    # on the page, so the two ways of editing it write to the same place.
    declared = widgets.labels_for(widgets.widget_key(section))
    if declared:
        text = dict(cfg.get('text') or {})
        for key in declared:
            if f'txt_{key}' in data:
                text[key] = (data.get(f'txt_{key}') or '').strip()[:400]
        if text:
            cfg['text'] = text

    if cfg != (section.config or {}):
        section.config = cfg
        changed.append('config')

    if changed:
        section.save()

    # Narrowing a row's layout would orphan blocks in columns that no longer
    # exist, so they are pulled back into the last remaining column instead of
    # silently disappearing.
    moved = 0
    if section.is_row:
        limit = section.column_count - 1
        stranded = section.children.filter(column__gt=limit)
        moved = stranded.count()
        if moved:
            stranded.update(column=limit)

    msg = 'Saved.'
    if moved:
        msg = f'Saved. {moved} block(s) moved into the last column.'
    return _block_json(request, section, msg)


@require_POST
@editor_required
def block_action(request, pk, action):
    """duplicate / delete / toggle / move / reparent."""
    section = _section(pk)
    page = section.page

    if action == 'delete':
        # Marked deleted rather than destroyed, so the editor can undo it. The
        # row keeps its order, its children and its content; restoring puts it
        # back exactly where it was.
        from django.utils import timezone
        label = section.get_type_display()
        kids = section.children.filter(deleted_at__isnull=True).count()
        now = timezone.now()
        Section.all_objects.filter(pk=section.pk).update(deleted_at=now)
        Section.all_objects.filter(parent_id=section.pk,
                                   deleted_at__isnull=True).update(deleted_at=now)
        note = f'{label} removed.'
        if kids:
            note = f'{label} and its {kids} nested block(s) removed.'
        return JsonResponse({'ok': True, 'message': note, 'removed': pk,
                             'undo': {'kind': 'restore', 'pk': section.pk}})

    if action == 'restore':
        # Undo for a delete. Children come back with their parent, which is why
        # they were stamped with the same moment.
        stamp = section.deleted_at
        Section.all_objects.filter(pk=section.pk).update(deleted_at=None)
        if stamp is not None:
            Section.all_objects.filter(parent_id=section.pk,
                                       deleted_at=stamp).update(deleted_at=None)
        section.refresh_from_db()
        return _block_json(request, section,
                           f'{section.get_type_display()} restored.')

    if action == 'purge':
        # The real delete, from the page manager rather than the editor.
        label = section.get_type_display()
        section.delete()
        return JsonResponse({'ok': True, 'message': f'{label} deleted for good.',
                             'removed': pk})

    if action == 'toggle':
        section.is_visible = not section.is_visible
        section.save(update_fields=['is_visible'])
        return _block_json(request, section,
                           'Block shown.' if section.is_visible else
                           'Block hidden from everyone but editors.')

    if action == 'duplicate':
        clone = Section.objects.get(pk=section.pk)
        clone.pk = None
        clone.order = section.order + 5
        clone.save()
        clone.audience_groups.set(section.audience_groups.all())
        for item in section.items.all():
            item.pk = None
            item.section = clone
            item.save()
        # A duplicated row has to bring its columns with it, or it arrives empty.
        for child in section.children.all():
            child_items = list(child.items.all())
            child.pk = None
            child.parent = clone
            child.save()
            for item in child_items:
                item.pk = None
                item.section = child
                item.save()
        return JsonResponse({
            'ok': True, 'message': 'Duplicated.', 'pk': clone.pk,
            'after': section.pk,
            'html': _render_block(request, clone),
        })

    if action == 'move':
        # Nudge up or down among its own siblings.
        data = _payload(request)
        try:
            delta = int(data.get('delta') or 0)
        except (TypeError, ValueError):
            delta = 0
        siblings = list(Section.objects.filter(page=page, parent=section.parent,
                                               column=section.column)
                        .order_by('-pinned', 'order', 'pk'))
        idx = next((i for i, s in enumerate(siblings) if s.pk == section.pk), None)
        if idx is None:
            raise Http404
        target = idx + (1 if delta > 0 else -1)
        if target < 0 or target >= len(siblings):
            return JsonResponse({'ok': True, 'message': 'Already at the end.',
                                 'noop': True})
        siblings[idx], siblings[target] = siblings[target], siblings[idx]
        for i, s in enumerate(siblings, start=1):
            Section.objects.filter(pk=s.pk).update(order=i * 10)
        return JsonResponse({'ok': True, 'message': 'Moved.', 'reload_page': True})

    if action == 'reparent':
        # Drag-and-drop landing: a new parent row, column and position.
        data = _payload(request)
        parent = None
        if data.get('parent'):
            parent = get_object_or_404(Section, pk=data['parent'], page=page)
            if not parent.is_row:
                return JsonResponse({'error': 'Target cannot hold blocks.'}, status=400)
            if section.is_row:
                return JsonResponse(
                    {'error': 'A columns row cannot be moved inside another row.'},
                    status=400)
            if parent.pk == section.pk:
                return JsonResponse({'error': 'A block cannot contain itself.'},
                                    status=400)
        try:
            column = max(0, int(data.get('column') or 0))
        except (TypeError, ValueError):
            column = 0
        if parent is not None:
            column = min(column, parent.column_count - 1)

        # A block dropped inside itself would detach that whole branch from the
        # page. Walking up from the target is the only way to catch the deeper
        # case, where the target is a grandchild rather than a direct child.
        node, seen = parent, set()
        while node is not None and node.pk not in seen:
            if node.pk == section.pk:
                return JsonResponse(
                    {'error': 'A block cannot be moved inside itself.'}, status=400)
            seen.add(node.pk)
            node = node.parent

        section.parent = parent
        section.column = column
        section.save(update_fields=['parent', 'column'])

        # The order arrives as a list over JSON, but a form-encoded post carries
        # it as one string. Iterating that string walked it character by
        # character and asked the database for a section with the id '[', which
        # was a 500 rather than a bad request.
        order = data.get('order') or []
        if isinstance(order, str):
            try:
                order = json.loads(order)
            except ValueError:
                order = []
        if not isinstance(order, (list, tuple)):
            order = []

        # Only ids belonging to this page are trusted, so a forged sequence
        # cannot reorder another page's blocks.
        for i, raw in enumerate(order, start=1):
            if str(raw).isdigit():
                Section.objects.filter(pk=str(raw), page=page).update(order=i * 10)
        return JsonResponse({'ok': True, 'message': 'Moved.', 'reload_page': True})

    return JsonResponse({'error': 'Unknown action.'}, status=400)


@require_POST
@editor_required
def item_reorder(request, section_pk):
    """Set the order of a block's rows from a dragged sequence.

    Rows were only orderable by typing numbers into a form, which is not a thing
    anyone should have to do to move a card left. The order arrives as the list of
    row ids in their new sequence.

    Ids that do not belong to this block are ignored rather than trusted, so a
    forged request cannot pull another block's rows into this one. Any row the
    client did not mention keeps its place at the end, so a stale page cannot
    silently discard rows it had not rendered — a hidden one, for instance.
    """
    section = get_object_or_404(Section, pk=section_pk)
    data = _payload(request)

    order = data.get('order') or []
    if isinstance(order, str):
        try:
            order = json.loads(order)
        except ValueError:
            order = []

    mine = {str(pk) for pk in section.items.values_list('pk', flat=True)}
    seen, seq = set(), []
    for raw in order:
        key = str(raw)
        if key in mine and key not in seen:
            seen.add(key)
            seq.append(key)

    if not seq:
        return JsonResponse({'error': 'Nothing recognisable to reorder.'}, status=400)

    was = [str(pk) for pk in
           section.items.order_by('order', 'pk').values_list('pk', flat=True)]

    for i, pk in enumerate(seq, start=1):
        SectionItem.objects.filter(pk=pk, section=section).update(order=i * 10)

    # Rows the client never sent — hidden ones it could not see — go after the
    # sequence rather than being left interleaved at stale positions.
    tail = section.items.exclude(pk__in=seq).order_by('order', 'pk')
    for j, item in enumerate(tail, start=len(seq) + 1):
        SectionItem.objects.filter(pk=item.pk).update(order=j * 10)

    response = _block_json(request, section, 'Order saved.')
    # The previous order travels back so a drag can be undone in one step.
    import json as _json
    payload = _json.loads(response.content)
    payload['undo'] = {'kind': 'item_order', 'pk': section.pk, 'order': was}
    return JsonResponse(payload)


@require_POST
@editor_required
def item_create(request, section_pk):
    section = get_object_or_404(Section, pk=section_pk)
    last = section.items.order_by('-order').first()
    item = SectionItem.objects.create(
        section=section, order=(last.order + 10) if last else 10,
        title='New item', icon='link', url='#')
    return _block_json(request, section, 'Item added.')


@require_POST
@editor_required
def item_delete(request, pk):
    item = get_object_or_404(SectionItem, pk=pk)
    section = item.section
    item.delete()
    return _block_json(request, section, 'Item removed.')


@require_POST
@editor_required
def preset_create(request, slug):
    """Insert a ready-made layout: the row and what goes in it, in one step.

    Getting two columns of text used to be: add a Columns block, choose its split,
    save, find the empty left column, add a text block, find the right one, add
    another. Five interactions before a word could be typed. This is one, and the
    whole thing arrives as a single undoable insert rather than five.
    """
    from .palette import PRESETS
    page = get_object_or_404(Page, slug=slug)
    key = (_payload(request).get('preset') or '').strip()

    preset = next((p for p in PRESETS if p['key'] == key), None)
    if preset is None:
        return JsonResponse({'error': 'Unknown layout.'}, status=400)

    last = Section.objects.filter(page=page, parent__isnull=True).order_by('-order').first()
    order = (last.order if last else 0) + 10

    wanted = [(t, col) for t, col in preset['children']
              if t in dict(SectionType.choices)]   # a type that no longer exists

    if preset['layout'] == 'full':
        # A stack, not a row. Wrapping these in a single-column row would add a
        # level of nesting for nothing and make every block inside it harder to
        # move out of.
        made = []
        for i, (child_type, _col) in enumerate(wanted):
            made.append(Section.objects.create(
                page=page, type=child_type, order=order + i * 10))
        if not made:
            return JsonResponse({'error': 'That layout has nothing in it.'}, status=400)
        return JsonResponse({
            'ok': True,
            'message': f'{preset["label"]} added.',
            'pk': made[0].pk,
            'blocks': [{'pk': m.pk, 'html': _render_block(request, m)} for m in made],
            'html': _render_block(request, made[0]),
        })

    row = Section.objects.create(
        page=page, type=SectionType.ROW, order=order, layout=preset['layout'])
    for i, (child_type, column) in enumerate(wanted, start=1):
        # Clamped to what the row actually has, so a preset can never leave a
        # block stranded in a column that is not drawn.
        Section.objects.create(
            page=page, type=child_type, parent=row,
            column=min(column, row.column_count - 1), order=i * 10)

    return JsonResponse({
        'ok': True,
        'message': f'{preset["label"]} added.',
        'pk': row.pk,
        'blocks': [{'pk': row.pk, 'html': _render_block(request, row)}],
        'html': _render_block(request, row),
    })


@editor_required
def page_layers(request, slug):
    """The page's structure as a tree, for the sidebar's Layers tab.

    The editor could only ever see a page as a flat run of blocks on the canvas,
    with nesting implied by where things happened to sit. A tree is how anyone
    used to a design tool expects to navigate a page: select a thing, see where it
    lives, drag it somewhere else.

    Read-only. Everything the tree does -- reorder, duplicate, hide, delete --
    goes through the endpoints that already exist for the canvas, so there is one
    implementation of each action rather than two that can disagree.
    """
    from .models import Page
    page = get_object_or_404(Page, slug=slug)

    def node(section):
        kids = []
        # Columns first, so a row reads in the order it is drawn.
        if section.is_row:
            for col in section.columns_for(request.user):
                kids.append({
                    'kind': 'column',
                    'label': f'Column {col["index"] + 1}',
                    'column': col['index'],
                    'parent': section.pk,
                    'children': [node(b) for b in col['blocks']],
                })
        items = list(section.items.all())
        return {
            'kind': 'block',
            'pk': section.pk,
            'type': section.type,
            'label': section.title or section.get_type_display(),
            'type_label': section.get_type_display(),
            # Only the four features that are applications. Every feature block
            # used to report locked, including the fifteen content panels, so the
            # tree told an editor fifteen things could not be opened that can.
            'locked': section.type == 'feature' and widgets.is_locked(section),
            'hidden': not section.is_visible,
            'item_count': len(items),
            'children': kids,
        }

    tops = [s for s in page.sections.filter(parent__isnull=True)]
    return JsonResponse({
        'ok': True,
        'page': {'slug': page.slug, 'title': page.title,
                 'path': page.bound_path, 'published': page.is_published},
        'tree': [node(s) for s in tops],
    })


@editor_required
def block_settings(request, pk):
    """The settings drawer contents for one block."""
    section = _section(pk)
    return JsonResponse({
        'ok': True,
        'pk': section.pk,
        'type': section.type,
        'type_label': section.get_type_display(),
        'is_row': section.is_row,
        'html': render_to_string('cms/partials/block_settings.html', {
            'section': section,
            'layouts': layout_options(),
            'audiences': Audience.choices,
            'widget_choices': widget_choices(),
            'data_driven': section.type in providers.PROVIDERS,
            # Set only for a retired block, so the editor is told what
            # replaced it rather than wondering why the library no
            # longer offers what is on their page.
            'replacement': palette.replacement_for(section.type),
            # What one row is called, so the Add button reads properly for
            # each block rather than saying "a row" everywhere.
            'row_word': {'page_head': 'an action', 'text_stats': 'a statistic',
                         'value_cards': 'a value',
                         }.get(section.type, 'a tile'),
            **_feature_context(section),
        }, request=request),
    })


def _feature_context(section):
    """What the drawer needs to show for a feature block.

    A content panel gets its editable words and a link to where its records are
    managed. A system feature gets neither, and the drawer says why. Before this
    every feature got the same "its inside is not editable" note, including the
    fifteen where it is.
    """
    if section.type != SectionType.FEATURE:
        return {}
    key = widgets.widget_key(section)
    locked = widgets.is_locked(section)
    stored = (section.config or {}).get('text') or {}
    labels = []
    for k, (default, hint) in widgets.labels_for(key).items():
        value = stored.get(k, default)
        # A label whose content is markup -- a bullet list, a run of paragraphs
        # -- is edited on the page, where it is a real rich field. Putting it in
        # the drawer meant showing "<ul><li>Training &amp;amp; Development
        # policy</li>..." inside a one-line text box, which nobody can edit.
        rich = '<' in (default or '') or '<' in (value or '')
        labels.append({'key': k, 'value': value, 'hint': hint,
                       'overridden': k in stored, 'rich': rich})

    records = None
    if not locked:
        entry = widgets.records_for(key)
        if entry:
            records = {'url': entry[0], 'label': entry[1]}
    return {'feature_locked': locked, 'feature_labels': labels,
            'feature_records': records}


# ── drafts ────────────────────────────────────────────────────────────────
def _live_page(slug):
    """The published page for this slug, whether a draft or the page was given.

    Tolerates a slug that no longer resolves: a draft's slug stops existing the
    moment it is published or discarded, and a second click on Save should say
    there is nothing to publish rather than 404.
    """
    page = Page.objects.filter(slug=slug).first()
    if page is None:
        raise Http404('That page no longer exists.')
    return page.live_page


@require_POST
@editor_required
def draft_publish(request, slug):
    """Put the draft live.

    Publishing is a bigger permission than editing: an editor may draft all day,
    but putting it in front of the company is a separate decision.
    """
    from .models import user_can_publish_cms
    if not user_can_publish_cms(request.user):
        return JsonResponse(
            {'error': 'You can edit this page, but publishing needs a publisher.'},
            status=403)

    page = _live_page(slug)
    if not page.publish_draft(request.user):
        return JsonResponse({'error': 'There is nothing to publish.'}, status=400)
    return JsonResponse({'ok': True, 'message': 'Published.',
                         'redirect': page.get_absolute_url()})


@require_POST
@editor_required
def draft_discard(request, slug):
    """Throw the draft away. The published page was never touched."""
    page = _live_page(slug)
    if not page.discard_draft():
        return JsonResponse({'error': 'There is nothing to discard.'}, status=400)
    return JsonResponse({'ok': True, 'message': 'Draft discarded.',
                         'redirect': page.get_absolute_url()})


@editor_required
def draft_status(request, slug):
    """Whether a draft is open, and what is different about it.

    The count is what makes Save meaningful: "Save 4 changes" tells you there is
    something to publish, where a bare Save button does not.
    """
    page = _live_page(slug)
    draft = page.open_draft()
    if draft is None:
        return JsonResponse({'ok': True, 'draft': False})

    live = list(Section.objects.filter(page=page)
                .values_list('type', 'order', 'column', 'title', 'subtitle', 'body'))
    new = list(Section.objects.filter(page=draft)
               .values_list('type', 'order', 'column', 'title', 'subtitle', 'body'))
    # A count of what differs, not a diff: enough to tell somebody whether they
    # have unsaved work, without pretending to be version history.
    changed = abs(len(new) - len(live)) + sum(
        1 for a, b in zip(sorted(map(str, live)), sorted(map(str, new))) if a != b)
    return JsonResponse({
        'ok': True,
        'draft': True,
        'changes': changed,
        'started_by': (draft.draft_started_by.get_full_name()
                       or draft.draft_started_by.username) if draft.draft_started_by else '',
        'started_at': draft.draft_started_at.isoformat() if draft.draft_started_at else '',
    })
