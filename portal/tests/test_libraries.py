"""The document-library workspace.

These cover the two things that were actually broken rather than only the happy
path: a document added the familiar way could never land in a folder, and any
member of the Policies group could edit or delete a document in somebody else's
library. Both are asserted here so neither can come back quietly.
"""
import io
import logging
import zipfile

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from portal.models import (LibraryAccess, LibraryMember, LibraryRole, Policy,
                          PolicyAccessRequest, PolicyCategory, PolicyFolder)


def setUpModule():
    """Keep Django's 500 logger out of the way.

    On this Python it crashes while rendering the debug page it is trying to
    log, and the traceback from *that* replaces the real failure — so a broken
    assertion reads as an unrelated error deep inside copy.copy.
    """
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def a_zip(names):
    """A zip in memory. A name ending in '/' is a directory entry."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        for name in names:
            if name.endswith('/'):
                zf.writestr(zipfile.ZipInfo(name), b'')
            else:
                zf.writestr(name, b'%PDF-1.4 pretend\n')
    buf.seek(0)
    return SimpleUploadedFile('bundle.zip', buf.read(),
                              content_type='application/zip')


class LibraryWorkspaceTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.group = Group.objects.create(name='Policies')
        cls.sana = User.objects.create_user('sana', password='x',
                                            first_name='Sana', last_name='Yasin')
        cls.omar = User.objects.create_user('omar', password='x',
                                            first_name='Omar', last_name='Q')
        cls.reader = User.objects.create_user('reader', password='x')
        cls.sana.groups.add(cls.group)
        cls.omar.groups.add(cls.group)

    def setUp(self):
        self.client.force_login(self.sana)

    # ── the list ────────────────────────────────────────────────────────────

    def test_home_lists_only_what_you_run(self):
        mine = PolicyCategory.objects.create(name='QHSE Manuals', created_by=self.sana)
        theirs = PolicyCategory.objects.create(name='Finance SOPs', created_by=self.omar)
        html = self.client.get(reverse('libraries_home')).content.decode()
        self.assertIn(mine.name, html)
        self.assertNotIn(theirs.name, html)

    def test_a_deputy_sees_the_library_they_help_run(self):
        lib = PolicyCategory.objects.create(name='Shared Manuals', created_by=self.omar)
        lib.managers.add(self.sana)
        html = self.client.get(reverse('libraries_home')).content.decode()
        self.assertIn('Shared Manuals', html)

    def test_the_old_dashboard_leads_here(self):
        self.assertRedirects(self.client.get(reverse('policies_manage')),
                             reverse('libraries_home'))

    # ── making one ──────────────────────────────────────────────────────────

    def test_creation_settles_access_before_content(self):
        res = self.client.post(reverse('library_new'), {
            'name': 'Restricted Manuals',
            'description': 'Only for the few',
            'access': LibraryAccess.RESTRICTED,
            'allowed_users': [str(self.omar.pk)],
            'managers': [str(self.omar.pk)],
        })
        lib = PolicyCategory.objects.get(name='Restricted Manuals')
        self.assertRedirects(res, reverse('library_workspace', args=[lib.pk]))
        self.assertEqual(lib.access, LibraryAccess.RESTRICTED)
        self.assertEqual(lib.created_by, self.sana)
        self.assertFalse(lib.downloadable)              # the box was not ticked
        self.assertIn(self.omar, lib.allowed_users.all())
        self.assertIn(self.omar, lib.managers.all())
        # A reader outside the list must not even see it exists.
        self.assertFalse(lib.visible_to(self.reader))

    def test_the_owner_is_never_their_own_deputy(self):
        self.client.post(reverse('library_new'),
                         {'name': 'Mine', 'access': LibraryAccess.EVERYONE,
                          'managers': [str(self.sana.pk), str(self.omar.pk)]})
        lib = PolicyCategory.objects.get(name='Mine')
        self.assertEqual([u.pk for u in lib.managers.all()], [self.omar.pk])

    # ── the zip, exactly as it was ──────────────────────────────────────────

    def test_a_zip_becomes_the_folders_it_had(self):
        self.client.post(reverse('library_new'), {
            'name': 'Imported', 'access': LibraryAccess.EVERYONE,
            'archive': a_zip(['Quality/', 'Quality/ISO9001.pdf',
                              'Quality/Forms/Audit.pdf',
                              'Safety/Empty/',
                              'top-level.pdf']),
        })
        lib = PolicyCategory.objects.get(name='Imported')
        paths = sorted(f.full_path for f in lib.folders.all())
        self.assertEqual(paths, ['Quality', 'Quality / Forms', 'Safety',
                                 'Safety / Empty'])
        # The empty folder survived: somebody who zipped it meant it.
        self.assertEqual(lib.folders.get(name='Empty').documents.count(), 0)
        by_title = {p.title: p for p in lib.policies.all()}
        self.assertEqual(by_title['ISO9001'].folder.name, 'Quality')
        self.assertEqual(by_title['Audit'].folder.full_path, 'Quality / Forms')
        self.assertIsNone(by_title['top-level'].folder)

    def test_a_zip_cannot_write_outside_the_library(self):
        lib = PolicyCategory.objects.create(name='Guarded', created_by=self.sana)
        self.client.post(reverse('library_import', args=[lib.pk]),
                         {'archive': a_zip(['../escape.pdf', 'ok.pdf',
                                            '__MACOSX/junk.pdf', '.DS_Store'])})
        titles = sorted(p.title for p in lib.policies.all())
        self.assertEqual(titles, ['ok'])
        self.assertEqual(lib.folders.count(), 0)

    def test_a_zip_does_not_bring_office_leftovers_with_it(self):
        """~$ and ~WRL files are Word's lock and crash-recovery copies.

        A zip made from a folder somebody was working in carries them, and one
        arrived as a document called "~WRL2065" that nothing could open.
        """
        lib = PolicyCategory.objects.create(name='Tidy', created_by=self.sana)
        self.client.post(reverse('library_import', args=[lib.pk]),
                         {'archive': a_zip(['Report.docx', '~$Report.docx',
                                            '~WRL2065.tmp', 'Notes/~$Notes.docx',
                                            'Notes/Notes.docx'])})
        self.assertEqual(sorted(p.title for p in lib.policies.all()),
                         ['Notes', 'Report'])

    def test_something_that_is_not_a_zip_is_refused(self):
        lib = PolicyCategory.objects.create(name='Guarded', created_by=self.sana)
        self.client.post(reverse('library_import', args=[lib.pk]), {
            'archive': SimpleUploadedFile('notes.txt', b'hello',
                                          content_type='text/plain')})
        self.assertEqual(lib.policies.count(), 0)

    # ── the bug the user hit ────────────────────────────────────────────────

    def test_a_document_can_be_put_in_a_folder(self):
        """The complaint: uploading into a folder showed no folder.

        The familiar add-a-document path had no folder field at all, so anything
        added through it sat at the top of the library whatever folder was open.
        """
        lib = PolicyCategory.objects.create(name='Manuals', created_by=self.sana)
        fld = PolicyFolder.objects.create(library=lib, name='QHSE')
        self.client.post(reverse('policy_files_upload', args=[lib.pk]), {
            'folder': str(fld.pk), 'doc_type': 'sop',
            'files': SimpleUploadedFile('Site-Manual.pdf', b'%PDF-1.4'),
        })
        doc = lib.policies.get()
        self.assertEqual(doc.folder, fld)
        # And the folder is on screen, with the document counted in it.
        html = self.client.get(reverse('library_workspace', args=[lib.pk])).content.decode()
        self.assertIn('QHSE', html)
        self.assertIn('1 document', html)

    def test_a_document_can_be_moved_between_folders(self):
        lib = PolicyCategory.objects.create(name='Manuals', created_by=self.sana)
        one = PolicyFolder.objects.create(library=lib, name='One')
        two = PolicyFolder.objects.create(library=lib, name='Two')
        doc = Policy.objects.create(category=lib, folder=one, title='Wandering')
        self.client.post(reverse('library_document_save', args=[doc.pk]),
                         {'title': 'Wandering', 'doc_type': 'policy',
                          'folder': str(two.pk)})
        doc.refresh_from_db()
        self.assertEqual(doc.folder, two)

    def test_a_document_cannot_be_moved_into_another_library(self):
        mine = PolicyCategory.objects.create(name='Mine', created_by=self.sana)
        theirs = PolicyCategory.objects.create(name='Theirs', created_by=self.omar)
        elsewhere = PolicyFolder.objects.create(library=theirs, name='Elsewhere')
        doc = Policy.objects.create(category=mine, title='Stays')
        self.client.post(reverse('library_document_save', args=[doc.pk]),
                         {'title': 'Stays', 'folder': str(elsewhere.pk)})
        doc.refresh_from_db()
        self.assertIsNone(doc.folder)

    # ── the overall view ────────────────────────────────────────────────────

    def test_all_documents_shows_every_one_with_its_path(self):
        lib = PolicyCategory.objects.create(name='Manuals', created_by=self.sana)
        deep = PolicyFolder.objects.create(
            library=lib, name='Inner',
            parent=PolicyFolder.objects.create(library=lib, name='Outer'))
        Policy.objects.create(category=lib, folder=deep, title='Buried Manual')
        Policy.objects.create(category=lib, title='Loose Manual')
        html = self.client.get(reverse('library_workspace', args=[lib.pk]),
                               {'tab': 'all'}).content.decode()
        self.assertIn('Buried Manual', html)
        self.assertIn('Outer / Inner', html)
        self.assertIn('Loose Manual', html)
        self.assertIn('Top of the library', html)

    def test_settings_holds_the_access_form_and_the_people_page_is_separate(self):
        """Three tabs, not four, and the roster drawn once.

        The access controls sat on a People tab that also drew a roster, so the
        same library had two lists of its people and neither looked like the real
        one. The controls are settings; who is in it is its own page.
        """
        lib = PolicyCategory.objects.create(name='Manuals', created_by=self.sana)
        settings = self.client.get(reverse('library_workspace', args=[lib.pk]),
                                   {'tab': 'settings'}).content.decode()
        self.assertIn('Name and tile', settings)
        self.assertIn('Who can open it', settings)
        self.assertIn('value="request"', settings)      # all three modes offered
        self.assertIn('Who helps you run it', settings)
        # And it points at the page that owns the people, rather than listing them.
        self.assertIn(reverse('library_users', args=[lib.pk]), settings)

        # The old tab names are in notification links, so they still land right.
        for name in ('people', 'requests'):
            self.assertRedirects(
                self.client.get(reverse('library_workspace', args=[lib.pk]),
                                {'tab': name}),
                reverse('library_users', args=[lib.pk]))

    def test_saving_one_form_does_not_wipe_the_other_half(self):
        """Both tabs post to one endpoint, so it must write only what it was sent.

        It set description and icon unconditionally, so saving who could open a
        library blanked its subtitle and its tile icon.
        """
        lib = PolicyCategory.objects.create(
            name='Manuals', description='Site operations', icon='books',
            created_by=self.sana, access=LibraryAccess.EVERYONE)
        lib.managers.add(self.omar)

        # The People form: access only.
        self.client.post(reverse('library_settings_save', args=[lib.pk]),
                         {'access': LibraryAccess.RESTRICTED,
                          'allowed_users': [str(self.reader.pk)]})
        lib.refresh_from_db()
        self.assertEqual(lib.description, 'Site operations')
        self.assertEqual(lib.icon, 'books')
        self.assertEqual(lib.access, LibraryAccess.RESTRICTED)
        # No managers field on that form, so the deputy stays.
        self.assertIn(self.omar, lib.managers.all())

        # The Settings form: name and tile only, and access is left alone.
        self.client.post(reverse('library_settings_save', args=[lib.pk]),
                         {'name': 'Renamed', 'description': 'New line',
                          'icon': 'folder'})
        lib.refresh_from_db()
        self.assertEqual(lib.name, 'Renamed')
        self.assertEqual(lib.access, LibraryAccess.RESTRICTED)
        self.assertIn(self.reader, lib.allowed_users.all())

    # ── removing one ────────────────────────────────────────────────────────

    def test_removing_a_library_takes_its_documents(self):
        """Policy.category is SET_NULL, so they used to be left behind.

        Ninety-nine documents from one deleted library ended up with no library
        at all, reachable only through an "Other Documents" tile that pointed at
        ?cat=0 and so opened nothing.
        """
        lib = PolicyCategory.objects.create(name='Doomed', created_by=self.sana)
        fld = PolicyFolder.objects.create(library=lib, name='Inside')
        Policy.objects.create(category=lib, folder=fld, title='Goes with it')
        Policy.objects.create(category=lib, title='Also goes')
        keep = PolicyCategory.objects.create(name='Kept', created_by=self.sana)
        Policy.objects.create(category=keep, title='Untouched')

        self.client.post(reverse('library_remove', args=[lib.pk]))
        self.assertFalse(PolicyCategory.objects.filter(pk=lib.pk).exists())
        self.assertFalse(PolicyFolder.objects.filter(pk=fld.pk).exists())
        self.assertEqual(Policy.objects.filter(category__isnull=True).count(), 0)
        self.assertEqual(Policy.objects.count(), 1)     # the other library's

    def test_no_other_documents_tile(self):
        """A tile linking to ?cat=0 matched no library, so it opened nothing."""
        self._bind_the_policies_page()
        PolicyCategory.objects.create(name='Real', created_by=self.sana)
        Policy.objects.create(category=None, title='Orphan')
        html = self.client.get(reverse('policies')).content.decode()
        self.assertNotIn('?cat=0', html)
        self.assertNotIn('Other Documents', html)

    # ── asking to be let in ─────────────────────────────────────────────────

    def _asked_for(self, name='Guarded'):
        lib = PolicyCategory.objects.create(name=name, created_by=self.sana,
                                            access=LibraryAccess.REQUEST)
        self.client.force_login(self.reader)
        return lib

    def test_a_request_carries_the_reason_given(self):
        lib = self._asked_for()
        self.client.post(reverse('policy_access_request', args=[lib.pk]),
                         {'message': 'Preparing the site audit.'})
        row = lib.access_requests.get()
        self.assertEqual(row.user, self.reader)
        self.assertEqual(row.message, 'Preparing the site audit.')

        # And the owner reads it on the people list, beside everybody else.
        self.client.force_login(self.sana)
        html = self.client.get(reverse('library_users', args=[lib.pk])).content.decode()
        self.assertIn('Preparing the site audit.', html)
        self.assertIn('Waiting', html)

    def test_a_request_without_a_reason_still_works(self):
        """The box is optional, so an empty one must not block the request."""
        lib = self._asked_for()
        self.client.post(reverse('policy_access_request', args=[lib.pk]), {})
        row = lib.access_requests.get()
        self.assertEqual(row.message, '')
        # No reason is not a blocker; the row simply carries none.
        self.client.force_login(self.sana)
        html = self.client.get(reverse('library_users', args=[lib.pk])).content.decode()
        self.assertIn(row.user.username, html)
        self.assertIn('Waiting', html)

    def test_the_tile_offers_the_reason_box(self):
        """The dialog is what collects it, so the tile must ask for one."""
        self._bind_the_policies_page()
        lib = self._asked_for()
        html = self.client.get(reverse('policies')).content.decode()
        self.assertIn('data-confirm-note', html)
        self.assertIn('data-confirm-note-name="message"', html)

    def test_the_ask_control_shares_the_icon_line(self):
        """So a closed tile is no taller than an open one.

        The button used to sit under the description, which made one tile taller
        and the grid stretched every other tile in the row to match.
        """
        self._bind_the_policies_page()
        self._asked_for()
        html = self.client.get(reverse('policies')).content.decode()
        tile = html[html.index('tile tile-shut'):]
        self.assertIn('tile-top', tile[:200])
        # The control comes before the heading, so it is inside the icon row.
        self.assertLess(tile.index('pol-ask'), tile.index('<h4>'))

    def test_access_withdrawn_after_approval_can_be_asked_for_again(self):
        """An approved row is history, not permission.

        request_state read the stored status, so somebody approved and later
        removed from the library's people saw a tile that looked open, opened
        nothing, and carried no way to ask again. Two real people were in that
        state. `allows` is the gate, so the tile follows it.
        """
        lib = self._asked_for()
        self.client.post(reverse('policy_access_request', args=[lib.pk]),
                         {'message': 'Let me in'})

        # Approved: they are in, and there is nothing to ask.
        self.client.force_login(self.sana)
        row = lib.access_requests.get()
        self.client.post(reverse('policy_request_decide', args=[row.pk]),
                         {'decision': 'approve'})
        self.assertTrue(lib.allows(self.reader))
        self.assertEqual(lib.request_state(self.reader), '')

        # Withdrawn again from the People tab — the row still says approved.
        lib.allowed_users.remove(self.reader)
        row.refresh_from_db()
        self.assertEqual(row.status, PolicyAccessRequest.Status.APPROVED)
        self.assertFalse(lib.allows(self.reader))
        self.assertEqual(lib.request_state(self.reader), 'open')

        # And the tile carries the button, so they are not stuck.
        self._bind_the_policies_page()
        self.client.force_login(self.reader)
        html = self.client.get(reverse('policies')).content.decode()
        self.assertIn(reverse('policy_access_request', args=[lib.pk]), html)
        self.assertIn('data-confirm-note', html)

    def test_waiting_sorts_above_everyone_already_in(self):
        lib = self._asked_for()
        self.client.post(reverse('policy_access_request', args=[lib.pk]),
                         {'message': 'Still waiting'})
        refused = PolicyAccessRequest.objects.create(
            library=lib, user=self.omar, message='Already answered',
            status=PolicyAccessRequest.Status.DECLINED)

        self.client.force_login(self.sana)
        res = self.client.get(reverse('library_users', args=[lib.pk]))
        states = [r['state'] for r in res.context['rows']]
        self.assertEqual(states[0], 'pending')
        self.assertIn('declined', states)
        self.assertTrue(res.context['takes_requests'])
        self.assertEqual(res.context['waiting'], 1)

    def test_the_queue_is_not_on_the_settings_tab(self):
        lib = self._asked_for()
        self.client.post(reverse('policy_access_request', args=[lib.pk]),
                         {'message': 'Let me in'})
        self.client.force_login(self.sana)
        html = self.client.get(reverse('library_workspace', args=[lib.pk]),
                               {'tab': 'settings'}).content.decode()
        self.assertNotIn('Let me in', html)
        self.assertNotIn('Approve', html)
        # But it says there is something waiting, and where to answer it.
        self.assertIn(reverse('library_users', args=[lib.pk]), html)

    def test_a_library_nobody_can_request_says_so(self):
        lib = PolicyCategory.objects.create(name='Closed', created_by=self.sana,
                                            access=LibraryAccess.RESTRICTED)
        res = self.client.get(reverse('library_users', args=[lib.pk]))
        self.assertFalse(res.context['takes_requests'])
        self.assertIn('Nobody can request this library', res.content.decode())

    # ── the people list ─────────────────────────────────────────────────────

    def test_the_people_list_holds_everyone_in_one_place(self):
        """In, helping run it, and waiting — one list, not a roster and a queue."""
        lib = PolicyCategory.objects.create(name='Shared', created_by=self.sana,
                                            access=LibraryAccess.REQUEST)
        lib.managers.add(self.omar)
        inside = User.objects.create_user('inside', password='x')
        lib.allowed_users.add(inside)
        PolicyAccessRequest.objects.create(library=lib, user=self.reader,
                                           message='Need the manuals')

        res = self.client.get(reverse('library_users', args=[lib.pk]))
        states = {r['user'].username: r['state'] for r in res.context['rows']}
        self.assertEqual(states[self.sana.username], 'owner')
        self.assertEqual(states[self.omar.username], 'deputy')
        self.assertEqual(states['inside'], 'in')
        self.assertEqual(states[self.reader.username], 'pending')
        self.assertEqual(res.context['waiting'], 1)
        self.assertEqual(res.context['inside'], 3)
        # Whoever is waiting comes first: they are the only row needing an answer.
        self.assertEqual(res.context['rows'][0]['state'], 'pending')

    def test_a_deputy_is_listed_once_not_twice(self):
        """Being a deputy and being named are two reasons to be in, one row."""
        lib = PolicyCategory.objects.create(name='Shared', created_by=self.sana)
        lib.managers.add(self.omar)
        lib.allowed_users.add(self.omar)
        res = self.client.get(reverse('library_users', args=[lib.pk]))
        rows = [r for r in res.context['rows'] if r['user'] == self.omar]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['state'], 'deputy')

    def test_accepting_from_the_list_answers_without_a_reload(self):
        lib = PolicyCategory.objects.create(name='Shared', created_by=self.sana,
                                            access=LibraryAccess.REQUEST)
        row = PolicyAccessRequest.objects.create(library=lib, user=self.reader)

        res = self.client.post(reverse('library_user_set', args=[lib.pk]),
                               {'user': self.reader.pk, 'action': 'accept'})
        self.assertEqual(res['Content-Type'], 'application/json')
        body = res.json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['state'], 'in')
        self.assertEqual(body['waiting'], 0)
        self.assertTrue(lib.allows(self.reader))
        row.refresh_from_db()
        self.assertEqual(row.status, PolicyAccessRequest.Status.APPROVED)

    def test_taking_someone_out_from_the_list(self):
        lib = PolicyCategory.objects.create(name='Shared', created_by=self.sana,
                                            access=LibraryAccess.REQUEST)
        lib.allowed_users.add(self.reader)
        PolicyAccessRequest.objects.create(
            library=lib, user=self.reader,
            status=PolicyAccessRequest.Status.APPROVED)

        body = self.client.post(reverse('library_user_set', args=[lib.pk]),
                                {'user': self.reader.pk, 'action': 'remove'}).json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['state'], 'declined')
        self.assertFalse(lib.allows(self.reader))
        # And they can ask again. 'declined' rather than 'open' because taking
        # somebody out who had asked *is* answering them — the tile says "Ask
        # again", which is truer than "Request" for somebody who had it before.
        self.assertEqual(lib.request_state(self.reader), 'declined')

    def test_the_owner_cannot_be_taken_out_of_their_own_library(self):
        lib = PolicyCategory.objects.create(name='Shared', created_by=self.sana)
        res = self.client.post(reverse('library_user_set', args=[lib.pk]),
                               {'user': self.sana.pk, 'action': 'remove'})
        self.assertEqual(res.status_code, 400)
        self.assertIn('owner', res.json()['error'])

    def test_nonsense_is_refused_rather_than_guessed_at(self):
        lib = PolicyCategory.objects.create(name='Shared', created_by=self.sana)
        for post in ({'user': self.reader.pk, 'action': 'promote'},
                     {'user': 99999, 'action': 'accept'},
                     {}):
            res = self.client.post(reverse('library_user_set', args=[lib.pk]), post)
            self.assertEqual(res.status_code, 400, post)

    def test_the_people_list_is_only_for_whoever_runs_the_library(self):
        theirs = PolicyCategory.objects.create(name='Theirs', created_by=self.omar)
        self.assertEqual(
            self.client.get(reverse('library_users', args=[theirs.pk])).status_code, 404)
        self.assertEqual(
            self.client.post(reverse('library_user_set', args=[theirs.pk]),
                             {'user': self.reader.pk, 'action': 'accept'}).status_code, 404)

    # ── what each person may do ─────────────────────────────────────────────

    def test_being_let_in_starts_at_read_and_download(self):
        """The level a named person already had before levels existed.

        Defaulting to read-only would have taken downloads away from everybody
        who has them, dressed up as a schema change.
        """
        lib = PolicyCategory.objects.create(name='Shared', created_by=self.sana,
                                            access=LibraryAccess.RESTRICTED)
        lib.allowed_users.add(self.reader)
        self.assertEqual(lib.memberships.get(user=self.reader).role,
                         LibraryRole.DOWNLOAD)
        self.assertEqual(lib.role_for(self.reader), LibraryRole.DOWNLOAD)

    def test_a_read_only_person_cannot_download_anything(self):
        lib = PolicyCategory.objects.create(name='Shared', created_by=self.sana,
                                            access=LibraryAccess.RESTRICTED,
                                            downloadable=True)
        lib.allowed_users.add(self.reader)
        doc = Policy.objects.create(category=lib, title='Manual',
                                    file=SimpleUploadedFile('m.pdf', b'%PDF-1.4'))
        self.assertTrue(doc.may_download(self.reader))

        LibraryMember.objects.filter(policycategory=lib, user=self.reader).update(
            role=LibraryRole.READ)
        lib = PolicyCategory.objects.get(pk=lib.pk)
        doc = Policy.objects.get(pk=doc.pk)
        self.assertTrue(doc.allows(self.reader))        # still reads it
        self.assertFalse(doc.may_download(self.reader))  # but keeps no copy

        # And a single document marked downloadable does not override the person.
        doc.downloadable = True
        doc.save(update_fields=['downloadable'])
        self.assertFalse(Policy.objects.get(pk=doc.pk).may_download(self.reader))

    def test_whoever_runs_the_library_always_gets_the_full_level(self):
        lib = PolicyCategory.objects.create(name='Shared', created_by=self.sana,
                                            access=LibraryAccess.RESTRICTED)
        lib.managers.add(self.omar)
        lib.allowed_users.add(self.omar)
        LibraryMember.objects.filter(policycategory=lib, user=self.omar).update(
            role=LibraryRole.READ)
        # They add and remove the files, so withholding a copy would be theatre.
        self.assertEqual(lib.role_for(self.omar), LibraryRole.DOWNLOAD)
        self.assertEqual(lib.role_for(self.sana), LibraryRole.DOWNLOAD)

    def test_the_level_can_be_changed_from_the_people_page(self):
        lib = PolicyCategory.objects.create(name='Shared', created_by=self.sana,
                                            access=LibraryAccess.RESTRICTED)
        lib.allowed_users.add(self.reader)
        body = self.client.post(reverse('library_user_set', args=[lib.pk]),
                                {'user': self.reader.pk, 'action': 'level',
                                 'role': LibraryRole.READ}).json()
        self.assertTrue(body['ok'])
        self.assertEqual(body['role'], LibraryRole.READ)
        self.assertEqual(lib.role_for(self.reader), LibraryRole.READ)

        # The page shows it, so a reload agrees with what was just set.
        res = self.client.get(reverse('library_users', args=[lib.pk]))
        row = next(r for r in res.context['rows'] if r['user'] == self.reader)
        self.assertEqual(row['role'], LibraryRole.READ)

    def test_a_level_cannot_be_set_for_somebody_with_no_access(self):
        """It would be a permission waiting for the day they are let in."""
        lib = PolicyCategory.objects.create(name='Shared', created_by=self.sana,
                                            access=LibraryAccess.RESTRICTED)
        res = self.client.post(reverse('library_user_set', args=[lib.pk]),
                               {'user': self.reader.pk, 'action': 'level',
                                'role': LibraryRole.READ})
        self.assertEqual(res.status_code, 400)
        self.assertIn('do not have access', res.json()['error'])

    def test_a_nonsense_level_is_refused(self):
        lib = PolicyCategory.objects.create(name='Shared', created_by=self.sana)
        lib.allowed_users.add(self.reader)
        res = self.client.post(reverse('library_user_set', args=[lib.pk]),
                               {'user': self.reader.pk, 'action': 'level',
                                'role': 'god-mode'})
        self.assertEqual(res.status_code, 400)

    # ── whose library it is ─────────────────────────────────────────────────

    def test_somebody_elses_library_is_not_reachable(self):
        theirs = PolicyCategory.objects.create(name='Theirs', created_by=self.omar)
        for name, args in (('library_workspace', [theirs.pk]),
                           ('library_settings_save', [theirs.pk]),
                           ('library_import', [theirs.pk]),
                           ('library_remove', [theirs.pk])):
            url = reverse(name, args=args)
            res = (self.client.get(url) if name == 'library_workspace'
                   else self.client.post(url, {}))
            self.assertEqual(res.status_code, 404, url)

    def test_the_old_endpoints_check_the_library_now(self):
        """Group membership used to be enough to delete anybody's document."""
        theirs = PolicyCategory.objects.create(name='Theirs', created_by=self.omar)
        doc = Policy.objects.create(category=theirs, title='Not yours')
        for name in ('policy_edit', 'policy_delete', 'library_document_save',
                     'library_document_remove'):
            res = self.client.post(reverse(name, args=[doc.pk]), {'title': 'Hijacked'})
            self.assertEqual(res.status_code, 404, name)
        doc.refresh_from_db()
        self.assertEqual(doc.title, 'Not yours')
        res = self.client.post(reverse('policy_save', args=[theirs.pk]),
                               {'title': 'Snuck in'})
        self.assertEqual(res.status_code, 404)
        self.assertEqual(theirs.policies.count(), 1)

    def test_a_reader_cannot_reach_the_workspace_at_all(self):
        lib = PolicyCategory.objects.create(name='Manuals', created_by=self.sana)
        self.client.force_login(self.reader)
        self.assertEqual(
            self.client.get(reverse('libraries_home')).status_code, 404)
        self.assertEqual(
            self.client.get(reverse('library_workspace', args=[lib.pk])).status_code, 404)

    # ── the reader's page is a reader's page ────────────────────────────────

    def _bind_the_policies_page(self):
        """The reader page's working part is a CMS feature block.

        Without a page bound to /policies/ the partial under test never renders
        at all, and every assertion about it passes vacuously.
        """
        from cms.models import Page, Section, SectionType
        page = Page.objects.create(title='Policies', slug='policies-test',
                                   bound_path='/policies/', is_published=True)
        Section.objects.create(page=page, type=SectionType.FEATURE,
                               config={'widget': 'policies'})

    def test_the_tile_grid_offers_a_way_in(self):
        """Reachable without opening a library first.

        The buttons through to the workspace were only in the header of an
        *opened* library, so from the tile grid there was no way to reach your
        own libraries and no way at all to create one.
        """
        self._bind_the_policies_page()
        PolicyCategory.objects.create(name='Manuals', created_by=self.sana)
        html = self.client.get(reverse('policies')).content.decode()
        self.assertIn(reverse('library_new'), html)
        self.assertIn(reverse('libraries_home'), html)

        # And it is addressed only to the people it applies to.
        self.client.force_login(self.reader)
        html = self.client.get(reverse('policies')).content.decode()
        self.assertNotIn(reverse('library_new'), html)

    def test_folders_show_instead_of_an_empty_message(self):
        """A folder holding only folders must not announce that it is empty.

        Folders and documents were two separate boxes, and the documents box
        carried its own "no documents yet" card whether or not there were
        folders above it — so a library full of folders said it was empty,
        directly underneath the folders it was listing.
        """
        self._bind_the_policies_page()
        lib = PolicyCategory.objects.create(name='Supervision', created_by=self.sana)
        top = PolicyFolder.objects.create(library=lib, name='Appendices')
        PolicyFolder.objects.create(library=lib, parent=top, name='Job Descriptions')

        for where in ({'cat': lib.pk}, {'cat': lib.pk, 'folder': top.pk}):
            html = self.client.get(reverse('policies'), where).content.decode()
            self.assertIn('pol-list', html, where)
            self.assertNotIn('pol-nothing', html, where)
            self.assertNotIn('No documents', html, where)

        # Genuinely empty, though, still says so.
        bare = PolicyCategory.objects.create(name='Bare', created_by=self.sana)
        html = self.client.get(reverse('policies'), {'cat': bare.pk}).content.decode()
        self.assertIn('pol-nothing', html)

    def test_folders_come_before_files_in_one_list(self):
        self._bind_the_policies_page()
        lib = PolicyCategory.objects.create(name='Mixed', created_by=self.sana)
        PolicyFolder.objects.create(library=lib, name='Zulu Folder')
        Policy.objects.create(category=lib, title='Alpha Document')
        html = self.client.get(reverse('policies'), {'cat': lib.pk}).content.decode()
        self.assertLess(html.index('Zulu Folder'), html.index('Alpha Document'))
        # One container holds both, not one each.
        self.assertEqual(html.count('class="pol-list'), 1)

    def test_no_management_forms_on_the_reader_page(self):
        """Browsable, not editable — and one way through to the workspace."""
        self._bind_the_policies_page()
        lib = PolicyCategory.objects.create(name='Manuals', created_by=self.sana)
        PolicyFolder.objects.create(library=lib, name='QHSE')
        html = self.client.get(reverse('policies'),
                               {'cat': lib.pk}).content.decode()
        self.assertIn('Manuals', html)                    # the block did render
        self.assertIn('QHSE', html)                       # still browsable
        self.assertNotIn('data-drop', html)               # but not editable
        self.assertNotIn('Remove folder', html)
        self.assertIn(reverse('library_workspace', args=[lib.pk]), html)
