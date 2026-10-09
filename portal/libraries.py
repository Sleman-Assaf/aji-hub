"""Document libraries — one screen to run them.

Running a library used to be spread across three places that did not know about
each other: `/policies/manage/` held the documents, `/policies/library/<pk>/
settings/` held who could see them, and the reader's own library page had grown
folder tools bolted onto it. Nothing showed a library whole, adding a document
from the familiar screen could not put it in a folder at all, and there was no
answer to "what is actually in here?".

This module is the replacement: a list of the libraries you run, and for each
one a workspace with its folders, its documents and its settings in the same
place. The reader's page stays a reader's page.

The order of the creation form is deliberate and is the thing that was missing.
Who may open a library, and whether its documents can leave the Hub, are decided
*before* the first file is uploaded — because those are the questions somebody
has already answered in their head by the time they are holding the documents,
and asking afterwards means every file spent some time under the wrong rule.
"""
import os
import re
import zipfile

from django.contrib.auth.models import Group, User
from django.core.files.base import ContentFile
from django.db.models import Count, Q
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .models import (LibraryAccess, LibraryRole, Policy, PolicyAccessRequest,
                     PolicyCategory, PolicyFolder)
from .views import (_library_for_manage, _save_access, _tell,
                    policies_group_required)


# ── what a person may run ────────────────────────────────────────────────────

def _manageable(user):
    """Every library this person may run.

    Ownership, deputyship and the legacy no-owner case are three different
    reasons to appear here, so this is one query with three branches rather than
    a Python loop over `can_manage` — the list is what the screen is, and it has
    to be countable and sortable.
    """
    from core.models import is_intranet_admin, user_can_manage_policies
    qs = PolicyCategory.objects.all()
    if not is_intranet_admin(user):
        mine = Q(created_by=user) | Q(managers=user)
        if user_can_manage_policies(user):
            # A library from before ownership existed belongs to nobody, and is
            # left to the Policies group rather than orphaned. This mirrors the
            # last branch of PolicyCategory.can_manage; if that changes, so must
            # this, or the list and the permission will disagree.
            mine |= Q(created_by__isnull=True)
        qs = qs.filter(mine)
    return qs.distinct()


def _with_counts(qs):
    """Documents, folders and unanswered requests per library, in one pass."""
    return qs.annotate(
        n_docs=Count('policies', distinct=True),
        n_folders=Count('folders', distinct=True),
        n_pending=Count('access_requests', distinct=True,
                        filter=Q(access_requests__status=PolicyAccessRequest.Status.PENDING)),
    )


# ── the list of libraries you run ────────────────────────────────────────────

@policies_group_required
def libraries_home(request):
    """Every library this person runs, and the way to make another."""
    libraries = list(_with_counts(_manageable(request.user))
                     .select_related('created_by')
                     .order_by('order', 'name'))
    for lib in libraries:
        lib.is_owner = (lib.created_by_id == request.user.pk)
    return render(request, 'portal/libraries_home.html', {
        'libraries': libraries,
        'waiting': sum(lib.n_pending for lib in libraries),
    })


# ── making one ───────────────────────────────────────────────────────────────

class _Nothing:
    """An empty related manager, for a library that has no row yet."""

    @staticmethod
    def all():
        return ()


class _NewLibrary:
    """A library that does not exist yet.

    `_access_fields.html` is shared with the settings tab and reads `access`,
    `downloadable` and both `allowed_*` lists off whatever it is given. An unsaved
    model instance cannot answer the last two — Django refuses to touch a
    many-to-many before the row has an id — so this answers them with nothing,
    which is what a library nobody has configured has. The defaults match the
    model's so the form opens on the same choice the database would have made.
    """
    name = description = icon = ''
    access = LibraryAccess.EVERYONE
    downloadable = True
    allowed_groups = _Nothing()
    allowed_users = _Nothing()



@policies_group_required
def library_new(request):
    """The creation form, and the library it makes.

    Everything the form asks for is settled before anything is uploaded: the
    name, who may open it, whether its documents may be saved, who helps run
    it — and only then the archive or the first files.
    """
    if request.method != 'POST':
        return render(request, 'portal/library_new.html', {
            'blank':      _NewLibrary(),
            'all_groups': Group.objects.order_by('name'),
            'people_url': reverse('policy_people_search'),
        })

    name = (request.POST.get('name') or '').strip()[:200]
    if not name:
        _tell(request, 'A library needs a name.', 'error')
        return redirect('library_new')

    library = PolicyCategory.objects.create(
        name=name,
        description=(request.POST.get('description') or '').strip()[:300],
        icon=(request.POST.get('icon') or '').strip()[:60],
        order=_next_order(),
        created_by=request.user,
    )
    # Access before content, which is the whole point of the order of this form.
    _save_access(request, library, 'library')
    _set_managers(request, library)

    extra = ''
    if request.FILES.get('archive') is not None:
        ok, note = _import_archive(request, library, None,
                                   request.FILES['archive'])
        extra = ' ' + note
    _tell(request, f'"{name}" created.{extra}')
    return redirect(_workspace_url(library))


def _next_order(step=10):
    """Put a new library after the ones that exist, not on top of one."""
    highest = (PolicyCategory.objects.order_by('-order')
               .values_list('order', flat=True).first())
    return (highest or 0) + step


def _set_managers(request, library):
    """The deputies, from the people picker — never the owner themselves.

    An owner in their own deputy list reads as though the library could be taken
    away from them by removing a name, which is not what the list means.
    """
    ids = [int(v) for v in request.POST.getlist('managers') if str(v).isdigit()]
    ids = [i for i in ids if i != library.created_by_id]
    library.managers.set(User.objects.filter(pk__in=ids, is_active=True))


# ── the workspace ────────────────────────────────────────────────────────────

@policies_group_required
def library_workspace(request, pk):
    """One library, whole: its folders, its documents and its settings.

    Three views of the same library rather than three screens. `files` is the
    folder you are standing in, `all` is every document in the library with the
    path it sits at — the answer to "what is actually in here?" that nothing used
    to give — and `settings` is who may open it.
    """
    library = _library_for_manage(request, pk)
    tab = request.GET.get('tab') or 'files'
    # Who is in the library is its own page. It was a tab here as well for a
    # while, which meant the roster was drawn twice and neither copy was
    # obviously the real one. Both names those tabs had are still in notification
    # links, so they redirect rather than landing quietly on Files.
    if tab in ('people', 'requests'):
        return redirect('library_users', pk=library.pk)
    if tab not in {'files', 'all', 'settings'}:
        tab = 'files'

    folder = _folder_in(library, request.GET.get('folder'))
    folders = list(library.folders.select_related('parent')
                   .annotate(n_docs=Count('documents', distinct=True),
                             n_subs=Count('children', distinct=True))
                   .order_by('order', 'name'))
    by_pk = {f.pk: f for f in folders}
    here = folder.pk if folder else None

    ctx = {
        # `all_groups` is not settings-only: every document row carries the same
        # access form, and a missing group list would silently render each one
        # with no groups to choose rather than failing loudly.
        'all_groups': Group.objects.order_by('name'),
        'library':    library,
        'tab':        tab,
        'folder':     folder,
        'crumbs':     (folder.ancestors() + [folder]) if folder else [],
        'subfolders': [f for f in folders if f.parent_id == here],
        'tree':       _tree(folders),
        'folders':    folders,
        'doc_types':  Policy.DocType.choices,
        'n_docs':     library.policies.count(),
        'n_folders':  len(folders),
        # Counted on every tab, not only the one that lists them: the badge on
        # the Requests tab is the only thing that tells somebody there is
        # anything to answer, and a tab they have to open to find out is a tab
        # nobody opens.
        'n_pending':  library.access_requests.filter(
            status=PolicyAccessRequest.Status.PENDING).count(),
    }

    if tab == 'all':
        # Every document, with where it lives. Sorted by that path, so the flat
        # list still reads as the structure it came out of.
        docs = list(library.policies.select_related('folder').order_by('title'))
        for d in docs:
            d.where = by_pk[d.folder_id].full_path if d.folder_id in by_pk else ''
        ctx['all_documents'] = sorted(
            docs, key=lambda d: (d.where.lower(), d.title.lower()))
    else:
        ctx['documents'] = list(library.policies.filter(folder=folder)
                                .prefetch_related('allowed_groups', 'allowed_users')
                                .order_by('order', 'title'))

    if tab == 'settings':
        # The access controls and the deputies picker live here now. They were on
        # a People tab that also drew the roster, so the same library had two
        # lists of its people and neither looked like the real one.
        ctx.update({
            'managers':   list(library.managers.order_by('first_name', 'username')),
            'is_owner':   library.created_by_id == request.user.pk,
            'people_url': reverse('policy_people_search'),
        })
    return render(request, 'portal/library_workspace.html', ctx)


def _folder_in(library, raw):
    """The folder named by a query string, if it belongs to this library."""
    raw = (raw or '').strip()
    if not raw.isdigit():
        return None
    return (PolicyFolder.objects.filter(pk=int(raw), library=library)
            .select_related('parent').first())


def _tree(folders):
    """The folders as a flat list in tree order, each carrying its depth.

    Flat and indented rather than nested markup: a template cannot recurse
    without including itself, and the sidebar is one scrollable column anyway.
    Anything whose parent is missing from the list is left out rather than
    silently reparented to the top, so a broken tree looks broken here instead of
    lying about where a folder lives.
    """
    children = {}
    for f in folders:
        children.setdefault(f.parent_id, []).append(f)
    out = []

    def walk(parent_id, depth):
        for f in children.get(parent_id, []):
            f.depth_ = depth
            f.pad = 10 + depth * 14
            out.append(f)
            if depth < PolicyFolder.MAX_DEPTH:
                walk(f.pk, depth + 1)

    walk(None, 0)
    return out


def _workspace_url(library, folder=None, tab=None):
    """Back to the workspace, in the folder and the view being worked in."""
    bits = []
    if folder is not None:
        bits.append(f'folder={folder.pk}')
    if tab:
        bits.append(f'tab={tab}')
    url = reverse('library_workspace', args=[library.pk])
    return url + ('?' + '&'.join(bits) if bits else '')


# ── settings, saved from the workspace ───────────────────────────────────────

@policies_group_required
@require_POST
def library_settings_save(request, pk):
    """Whatever the form that posted here actually carried.

    Two forms post to this now — the library's name and mark under Settings, and
    who may open it under People. Each carries only its own fields, so this must
    write only the fields it was sent: setting `description` and `icon` from a
    form that does not have them blanked the subtitle and the tile icon every
    time somebody changed who could open the library.
    """
    library = _library_for_manage(request, pk)
    touched = []
    for field, limit in (('name', 200), ('description', 300), ('icon', 60)):
        if field not in request.POST:
            continue
        value = (request.POST.get(field) or '').strip()[:limit]
        if field == 'name' and not value:
            continue                      # a library with no name is not a save
        setattr(library, field, value)
        touched.append(field)
    if touched:
        library.save(update_fields=touched)
    _save_access(request, library, 'library')
    # Only the owner appoints deputies. A deputy who could appoint deputies is a
    # deputy who can hand the library to anybody, which is not what being asked
    # to help means — so their form does not carry the field at all, and this
    # refuses it a second time in case it is posted anyway.
    from core.models import is_intranet_admin
    if (request.POST.get('managers_set')
            and (library.created_by_id == request.user.pk
                 or is_intranet_admin(request.user))):
        _set_managers(request, library)
    _tell(request, 'Settings saved.')
    return redirect(_workspace_url(library, tab='settings'))


@policies_group_required
@require_POST
def library_remove(request, pk):
    """Remove a library and everything filed in it.

    The documents are deleted explicitly. `Policy.category` is SET_NULL, so
    deleting the library on its own left every document behind with no library —
    still in the database, still holding a file, and reachable only through an
    "Other Documents" tile that pointed at `?cat=0` and therefore opened
    nothing. One deleted library put 99 documents there. Folders do cascade, so
    they need no help.
    """
    library = _library_for_manage(request, pk)
    name = library.name
    docs = list(library.policies.all())
    for policy in docs:
        _forget_file(policy)
    library.policies.all().delete()
    library.delete()
    n = len(docs)
    _tell(request, f'"{name}" removed, with its {n} document{"" if n == 1 else "s"}.')
    return redirect('libraries_home')


# ── a document, from the workspace ───────────────────────────────────────────

def _document_for_manage(request, pk):
    """One document, if this person runs the library it is filed in.

    The old add, edit and delete endpoints checked only that the caller was in
    the Policies group, which let any member of it edit or delete a document in
    somebody else's library — the exact thing ownership exists to prevent.
    """
    policy = get_object_or_404(
        Policy.objects.select_related('category', 'folder'), pk=pk)
    if policy.category is None or not policy.category.can_manage(request.user):
        raise Http404
    return policy


@policies_group_required
@require_POST
def document_save(request, pk):
    """Retitle, retype, re-file and re-rule one document, in one form."""
    policy = _document_for_manage(request, pk)
    title = (request.POST.get('title') or '').strip()[:300]
    if title:
        policy.title = title
    policy.description = (request.POST.get('description') or '').strip()
    doc_type = request.POST.get('doc_type')
    if doc_type in Policy.DocType.values:
        policy.doc_type = doc_type

    # Moving a document between folders — the one thing folders were missing.
    # Only folders could be moved, so a file uploaded to the wrong place had to
    # be deleted and uploaded again.
    raw = (request.POST.get('folder') or '').strip()
    target = _folder_in(policy.category, raw) if raw else None
    policy.folder = target

    if 'file' in request.FILES:
        _forget_file(policy)
        policy.file = request.FILES['file']
    policy.save()
    _save_access(request, policy, 'document')
    _tell(request, f'"{policy.title}" saved.')
    return redirect(_workspace_url(policy.category, target))


@policies_group_required
@require_POST
def document_remove(request, pk):
    policy = _document_for_manage(request, pk)
    library, folder, title = policy.category, policy.folder, policy.title
    _forget_file(policy)
    policy.delete()
    _tell(request, f'"{title}" removed.')
    return redirect(_workspace_url(library, folder))


def _forget_file(policy):
    """Delete the file on disk, and never let that failure lose the record."""
    if not policy.file:
        return
    try:
        if os.path.isfile(policy.file.path):
            os.remove(policy.file.path)
    except Exception:
        pass


# ── a zip, as it was ─────────────────────────────────────────────────────────

#: Guards on an uploaded archive. A zip is the one upload whose cost is not
#: visible from its size, so all three are needed: a small file can carry a
#: hundred thousand entries, or expand until it fills the disk.
MAX_ENTRIES = 400
MAX_TOTAL = 400 * 1024 * 1024
MAX_ONE = 60 * 1024 * 1024
SKIP_DIRS = {'__MACOSX', '__MACOSX/', '.git', '.svn'}
SKIP_NAMES = {'.DS_Store', 'Thumbs.db', 'desktop.ini'}
#: Office's own leftovers. `~$Report.docx` is the lock file Word writes while a
#: document is open, and `~WRL1234.tmp` is its crash-recovery copy. Both end up
#: in a zip made from a folder somebody was working in, and one arrived as a
#: document called "~WRL2065" that nothing could open.
SKIP_PREFIXES = ('~$', '~WRL', '~WRD')


@policies_group_required
@require_POST
def library_import(request, pk):
    """Unpack an archive into this library, keeping its folders as they are."""
    library = _library_for_manage(request, pk)
    folder = _folder_in(library, request.POST.get('folder'))
    archive = request.FILES.get('archive')
    if archive is None:
        _tell(request, 'No archive was chosen.', 'error')
        return redirect(_workspace_url(library, folder))
    ok, note = _import_archive(request, library, folder, archive)
    _tell(request, note, 'success' if ok else 'error')
    return redirect(_workspace_url(library, folder))


def _import_archive(request, library, parent, upload):
    """Recreate an archive inside a library. Returns (ok, message).

    The folders come out as the archive has them, empty ones included: somebody
    who zipped a structure up expects that structure back, and an empty chapter
    folder is a statement that the chapter exists.
    """
    if not zipfile.is_zipfile(upload):
        return False, 'That file is not a zip archive.'
    upload.seek(0)

    doc_type = request.POST.get('doc_type')
    if doc_type not in Policy.DocType.values:
        doc_type = Policy.DocType.POLICY

    made_docs = skipped = 0
    clipped = False
    try:
        with zipfile.ZipFile(upload) as zf:
            entries = [i for i in zf.infolist() if _wanted(i)]
            if len(entries) > MAX_ENTRIES:
                return False, (f'That archive holds {len(entries)} items and the '
                               f'limit is {MAX_ENTRIES}. Split it up and import '
                               f'the parts.')
            if sum(i.file_size for i in entries) > MAX_TOTAL:
                return False, ('That archive unpacks to more than '
                               f'{MAX_TOTAL // (1024 * 1024)}MB.')

            # Directories first, so a file never has to create the folder it
            # lands in — and so the empty ones survive.
            cache = {(): parent}
            for info in entries:
                if info.is_dir():
                    _folder_at(library, _parts(info), cache)
            for info in entries:
                if info.is_dir():
                    continue
                parts = _parts(info)
                if info.file_size > MAX_ONE:
                    skipped += 1
                    continue
                where, cut = _folder_at(library, parts[:-1], cache)
                clipped = clipped or cut
                with zf.open(info) as fh:
                    blob = ContentFile(fh.read(), name=parts[-1])
                stem = parts[-1]
                Policy.objects.create(
                    category=library, folder=where, doc_type=doc_type,
                    title=(stem.rsplit('.', 1)[0] if '.' in stem else stem)[:300] or 'Document',
                    file=blob, order=99)
                made_docs += 1
            made_folders = len(cache) - 1
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        return False, f'That archive could not be read ({exc.__class__.__name__}).'

    if not made_docs and not made_folders:
        return False, 'Nothing in that archive could be imported.'
    bits = [f'{made_docs} document{"" if made_docs == 1 else "s"}']
    if made_folders:
        bits.append(f'{made_folders} folder{"" if made_folders == 1 else "s"}')
    note = ' and '.join(bits) + ' imported.'
    if skipped:
        note += f' {skipped} file{"" if skipped == 1 else "s"} too large to take.'
    if clipped:
        note += (f' Anything deeper than {PolicyFolder.MAX_DEPTH} folders was '
                 f'filed at that level.')
    return True, note


def _wanted(info):
    """Whether an archive entry is a real document or a packaging artefact."""
    name = info.filename.replace('\\', '/')
    if name.startswith('/') or '..' in name.split('/'):
        return False          # nothing may land outside the library it goes into
    parts = [p for p in name.split('/') if p]
    if not parts:
        return False
    if any(p in SKIP_DIRS for p in parts):
        return False
    if info.is_dir():
        return True
    leaf = parts[-1]
    if leaf in SKIP_NAMES or leaf.startswith('.'):
        return False
    if leaf.startswith(SKIP_PREFIXES):
        return False
    return True


def _parts(info):
    """The entry's path, cleaned into folder names."""
    out = []
    for p in info.filename.replace('\\', '/').split('/'):
        p = re.sub(r'[\r\n\t]', ' ', p).strip(' .')
        if p:
            out.append(p[:200])
    return out


def _folder_at(library, parts, cache):
    """The folder for a path inside the archive, creating what is missing.

    Returns (folder, clipped) — clipped says the path was deeper than the tree
    allows, and the document was filed at the deepest level rather than dropped,
    because losing a file is worse than filing it a level up.
    """
    parent = cache[()]
    base = (parent.depth + 1) if parent is not None else 0
    key = ()
    clipped = False
    for name in parts:
        if base + len(key) >= PolicyFolder.MAX_DEPTH:
            clipped = True
            break
        key = key + (name.lower(),)
        if key not in cache:
            cache[key] = PolicyFolder.objects.create(
                library=library, parent=parent, name=name, order=99)
        parent = cache[key]
    return parent, clipped


# ── the people, as one list ──────────────────────────────────────────────────

#: What a row on the users page is. Ordered as the page shows them: whoever is
#: waiting first, because that is the only part of the list that needs an answer.
ROW_ORDER = {'pending': 0, 'declined': 1, 'owner': 2, 'deputy': 3, 'in': 4}


@policies_group_required
def library_users(request, pk):
    """Everyone in this library and everyone asking to be, in one list.

    Its own page rather than a panel under the access settings. Those are two
    different jobs: the settings decide *how* the library is closed — open, by
    request, or named people only — and which groups it is open to. This decides
    which individual people are in it, and it is the only screen where letting
    somebody in and taking them out again are the same gesture.

    Accepting adds the person to `allowed_users`, which is the same list the
    settings edit. There is one gate, so a name can never be in the library
    without appearing here.
    """
    library = _library_for_manage(request, pk)
    rows = []

    if library.created_by:
        rows.append({'user': library.created_by, 'state': 'owner',
                     'note': 'Set this library up', 'when': library.created_at})
    deputies = set()
    for person in library.managers.order_by('first_name', 'username'):
        deputies.add(person.pk)
        rows.append({'user': person, 'state': 'deputy',
                     'note': 'Helps run it', 'when': None})

    members = {m.user_id: m for m in library.memberships.select_related('user')}
    named = {pk_: m.user for pk_, m in sorted(
        members.items(),
        key=lambda kv: (kv[1].user.get_full_name() or kv[1].user.username).lower())}
    asked = {r.user_id: r for r in library.access_requests.select_related('user')}

    for pk_, person in named.items():
        if pk_ == library.created_by_id or pk_ in deputies:
            continue                       # already listed, by a stronger claim
        row = asked.get(pk_)
        rows.append({'user': person, 'state': 'in',
                     'role': members[pk_].role,
                     'note': (row.message if row and row.message else
                              'Added directly' if not row else 'Approved'),
                     'when': row.resolved_at if row else None})

    for pk_, row in asked.items():
        if pk_ in named or pk_ == library.created_by_id or pk_ in deputies:
            continue                       # already in, so not waiting
        if row.status == PolicyAccessRequest.Status.PENDING:
            rows.append({'user': row.user, 'state': 'pending',
                         'note': row.message, 'when': row.created_at})
        else:
            # Declined, or approved and later taken out again. Either way they are
            # not in, and either way you can let them in from here.
            rows.append({'user': row.user, 'state': 'declined',
                         'note': row.message, 'when': row.resolved_at})

    rows.sort(key=lambda r: (ROW_ORDER.get(r['state'], 9),
                             (r['user'].get_full_name() or r['user'].username).lower()))
    return render(request, 'portal/library_users.html', {
        'library': library,
        'rows': rows,
        'waiting': sum(1 for r in rows if r['state'] == 'pending'),
        'inside': sum(1 for r in rows if r['state'] in ('owner', 'deputy', 'in')),
        'takes_requests': library.access == LibraryAccess.REQUEST,
        'is_open': library.access == LibraryAccess.EVERYONE,
    })


@policies_group_required
@require_POST
def library_user_set(request, pk):
    """Let one person in, or take them out. Answers JSON so nothing reloads.

    `allowed_users` is the gate, and the request row is the conversation about
    it — so both are kept straight here rather than in two endpoints that could
    disagree about whether somebody is in.
    """
    from django.utils import timezone
    library = _library_for_manage(request, pk)
    who = User.objects.filter(pk=request.POST.get('user'), is_active=True).first()
    action = request.POST.get('action')
    if who is None or action not in {'accept', 'remove', 'decline', 'level'}:
        return JsonResponse({'ok': False, 'error': 'Nothing to do.'}, status=400)
    if who.pk == library.created_by_id:
        return JsonResponse(
            {'ok': False, 'error': 'The owner cannot be removed from their own '
                                   'library.'}, status=400)

    row = library.access_requests.filter(user=who).first()

    if action == 'level':
        # Only what an existing member may do, never whether they are one. A
        # level for somebody who is not in the library would be a permission
        # waiting quietly for the day they are let in.
        level = request.POST.get('role')
        if level not in LibraryRole.values:
            return JsonResponse({'ok': False, 'error': 'Unknown level.'}, status=400)
        member = library.memberships.filter(user=who).first()
        if member is None:
            return JsonResponse(
                {'ok': False, 'error': 'They do not have access yet.'}, status=400)
        member.role = level
        member.save(update_fields=['role'])
        name = who.get_full_name() or who.username
        return JsonResponse({
            'ok': True, 'state': 'in', 'role': level,
            'said': (f'{name} can read and download.' if level == LibraryRole.DOWNLOAD
                     else f'{name} can read in the Hub only.'),
            'waiting': library.access_requests.filter(
                status=PolicyAccessRequest.Status.PENDING).count(),
        })

    if action == 'accept':
        # add() on a many-to-many with a through model creates the row with its
        # field defaults, which is the full level — the same thing everybody had
        # before levels existed.
        library.allowed_users.add(who)
        state = 'in'
    else:
        library.allowed_users.remove(who)
        state = 'declined' if row is not None else 'out'

    if row is not None:
        row.status = (PolicyAccessRequest.Status.APPROVED if action == 'accept'
                      else PolicyAccessRequest.Status.DECLINED)
        row.resolved_at = timezone.now()
        row.resolved_by = request.user
        row.save(update_fields=['status', 'resolved_at', 'resolved_by'])

    if library.managers.filter(pk=who.pk).exists():
        state = 'deputy'

    name = who.get_full_name() or who.username
    role = getattr(library.memberships.filter(user=who).first(), 'role', None)
    # Told only when they asked and are hearing back. Being taken off a list
    # nobody asked to be on is not news worth a notification.
    if row is not None:
        from core.models import NotificationEvent as _Event
        from core.notify import notify
        notify(who, _Event.GENERAL,
               title=(f'You can now open {library.name}' if action == 'accept'
                      else f'Your access to {library.name} was not granted'),
               body='' if action == 'accept' else 'Ask the library owner if you need it.',
               link=reverse('policies') + f'?cat={library.pk}',
               actor=request.user)

    return JsonResponse({
        'ok': True,
        'state': state,
        'role': role,
        'said': (f'{name} now has access.' if action == 'accept'
                 else f'{name} no longer has access.'),
        'waiting': library.access_requests.filter(
            status=PolicyAccessRequest.Status.PENDING).count(),
    })
