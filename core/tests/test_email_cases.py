"""The email registry, and the two halves of it that must not drift apart.

A case exists in two places: declared in `core.email_cases`, and named as
`case='...'` at the call site that sends it. Either half alone is a bug that is
invisible until somebody edits an email and nothing changes, or until an email
goes out that the Emails tab does not list. These tests fail on both.
"""
import ast
import os
import re

from django.contrib.auth.models import User
from django.test import TestCase

from core.email_cases import (ATTACHMENTS, CASES, CASES_BY_CODE, MODULE_LABELS,
                              by_module, current_text, examples_for, no_dashes,
                              resolve)
from core.models import EmailTemplate

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKIP_DIRS = {'.venv', '.git', 'migrations', '__pycache__', 'node_modules',
             'staticfiles', 'docs', 'tests'}

#: Cases with no call site of their own, and why. Anything not listed here has to
#: be reachable, so adding a case without wiring it fails this test.
UNWIRED = {
    # Written as bell notifications long before the notification service existed,
    # and still are. Declared so the wording is editable in the same place as
    # everything else and so switching them on is a decision rather than a
    # rewrite; every one ships switched off.
    'training.submitted', 'training.needs_manager', 'training.needs_hr',
    'training.manager_decided', 'training.hr_decided', 'training.scheduled',
    'training.completed', 'training.commented',
}


#: What a case code looks like: module.name, both halves lower case. Used to
#: tell an email case from any other string a function of the same name takes.
CODE = re.compile(r'^[a-z][a-z_]*\.[a-z][a-z_]*$')


def _source_files():
    for root, dirs, files in os.walk(REPO):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.endswith('.py'):
                yield os.path.join(root, name)


class RegistryShape(TestCase):

    def test_codes_are_unique_and_module_is_real(self):
        codes = [c.code for c in CASES]
        self.assertEqual(len(codes), len(set(codes)), 'duplicate case code')
        for c in CASES:
            self.assertIn(c.module, MODULE_LABELS,
                          f'{c.code} claims module {c.module!r}')

    def test_every_placeholder_used_is_declared(self):
        """A `{brace}` in the wording that is not in `placeholders` would render
        as literal text in a real email and never be filled in."""
        for c in CASES:
            used = set(re.findall(r'\{(\w+)\}', c.subject + ' ' + c.body))
            undeclared = used - set(c.placeholders)
            self.assertEqual(undeclared, set(),
                             f'{c.code} uses {undeclared} but does not declare them')

    def test_attachment_offers_are_real_automations(self):
        for c in CASES:
            for kind in c.attachments:
                self.assertIn(kind, ATTACHMENTS,
                              f'{c.code} offers an attachment nothing produces')
            if c.attach_by_default:
                self.assertIn(c.attach_by_default, c.attachments,
                              f'{c.code} defaults to an attachment it does not offer')

    def test_no_default_wording_carries_a_dash(self):
        """The house rule, checked against the defaults themselves. The
        sanitiser would catch one on the way out, but a default written with a
        dash means the editor shows wording that is not what gets sent."""
        for c in CASES:
            for label, text in (('subject', c.subject), ('body', c.body),
                                ('link label', c.link_label),
                                ('footer', c.footer)):
                self.assertEqual(no_dashes(text), text,
                                 f'{c.code} {label} contains a dash: {text!r}')


class EveryCaseIsReachable(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.named = set()
        cls.dynamic = 0
        for path in _source_files():
            try:
                tree = ast.parse(open(path, encoding='utf-8').read())
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue

                # Two shapes count as sending a case. Most go through notify(),
                # which takes `case=`. The three that build their own email,
                # because they carry an attachment, an item list or an unsaved
                # draft, call resolve() or test_send() with the code instead.
                for kw in node.keywords:
                    if kw.arg != 'case':
                        continue
                    if isinstance(kw.value, ast.Constant):
                        cls.named.add(kw.value.value)
                    else:
                        # `case=case_code` — a call site choosing between two
                        # cases at runtime. Counted, not resolved.
                        cls.dynamic += 1

                name = (getattr(node.func, 'id', None)
                        or getattr(node.func, 'attr', None))
                if name not in ('resolve', 'test_send'):
                    continue
                # `code=` is far too common a keyword to sweep on its own: the
                # job-requisition seeder passes `code='AJI-DEMO'` to an unrelated
                # get_or_create and was being read as an email case.
                candidates = list(node.args) + [kw.value for kw in node.keywords
                                                if kw.arg == 'code']
                for arg in candidates:
                    if isinstance(arg, ast.Constant) and CODE.match(str(arg.value)):
                        cls.named.add(arg.value)

    def test_every_declared_case_is_sent_from_somewhere(self):
        # Cases reached through a variable cannot be matched by name, so they are
        # allowed for explicitly rather than making this test guess.
        via_variable = {'athar.manager_assigned_open',
                        'athar.manager_assigned_upcoming'}
        self.assertGreater(self.dynamic, 0,
                           'expected at least one runtime-chosen case')
        expected = set(CASES_BY_CODE) - UNWIRED - via_variable
        missing = expected - self.named
        self.assertEqual(missing, set(),
                         f'declared but never sent: {sorted(missing)}')

    def test_no_call_site_names_a_case_that_does_not_exist(self):
        unknown = self.named - set(CASES_BY_CODE)
        self.assertEqual(unknown, set(),
                         f'sent but never declared: {sorted(unknown)}')

    def test_the_cases_that_never_email_are_switched_off(self):
        for code in UNWIRED:
            self.assertFalse(CASES_BY_CODE[code].enabled_by_default,
                             f'{code} has no call site but would send email')


class Resolving(TestCase):

    def test_the_default_is_used_until_something_overrides_it(self):
        found = CASES_BY_CODE['kudos.received']
        shape = resolve('kudos.received', {'actor': 'Nael'})
        self.assertEqual(shape.subject, found.subject.replace('{actor}', 'Nael'))

        EmailTemplate.objects.create(code='kudos.received',
                                     subject='Kudos for you from {actor}')
        shape = resolve('kudos.received', {'actor': 'Nael'})
        self.assertEqual(shape.subject, 'Kudos for you from Nael')

    def test_clearing_an_override_returns_to_the_default(self):
        row = EmailTemplate.objects.create(code='kudos.received', subject='Mine')
        row.subject = ''
        row.save()
        self.assertEqual(resolve('kudos.received', {'actor': 'X'}).subject,
                         'X sent you Kudos')

    def test_a_switched_off_case_reports_itself_as_off(self):
        EmailTemplate.objects.create(code='kudos.received', enabled=False)
        self.assertFalse(resolve('kudos.received', {}).enabled)

    def test_a_dash_typed_into_the_editor_never_reaches_the_email(self):
        EmailTemplate.objects.create(
            code='kudos.received',
            subject='Kudos — from {actor}',
            body='Well done — really.\nSee it – on Taqdeer.')
        shape = resolve('kudos.received', {'actor': 'Nael'})
        self.assertNotIn('—', shape.subject)
        self.assertNotIn('–', shape.subject)
        for line in shape.body_lines:
            self.assertNotIn('—', line)
            self.assertNotIn('–', line)
        self.assertEqual(shape.subject, 'Kudos, from Nael')

    def test_a_dash_arriving_inside_a_value_is_stripped_too(self):
        """The wording is not the only route in. A citation typed in Word, or a
        congratulations line from Taqdeer settings, is just as likely a source."""
        shape = resolve('athar.nomination_approved_winner', {
            'name': 'Lina', 'period': 'Q1 2026',
            'citation': 'outstanding delivery — twice over'})
        for line in shape.body_lines:
            self.assertNotIn('—', line)

    def test_a_salutation_keeps_its_comma(self):
        EmailTemplate.objects.create(code='kudos.received',
                                     body='Hello {name},\nWell done.')
        shape = resolve('kudos.received', {'name': 'Lina'})
        self.assertEqual(shape.body_lines[0], 'Hello Lina,')

    def test_an_unknown_placeholder_stays_visible_rather_than_raising(self):
        EmailTemplate.objects.create(code='kudos.received',
                                     subject='Hello {recipiant}')
        self.assertEqual(resolve('kudos.received', {'name': 'Lina'}).subject,
                         'Hello {recipiant}')

    def test_an_unknown_case_resolves_to_nothing(self):
        self.assertIsNone(resolve('nope.nope', {}))


class Filtering(TestCase):

    def test_a_module_filter_returns_only_that_module(self):
        rows = by_module('kudos')
        self.assertTrue(rows)
        self.assertEqual({c.module for c in rows}, {'kudos'})

    def test_no_filter_returns_everything(self):
        self.assertEqual(len(by_module()), len(CASES))

    def test_search_looks_at_the_label_the_module_and_when_it_fires(self):
        self.assertTrue(by_module(query='nominations'))
        self.assertTrue(by_module(query='Kudos'))
        self.assertEqual(by_module(query='zzzznothing'), [])


class Permissions(TestCase):

    def setUp(self):
        self.admin = User.objects.create_superuser('root', 'r@a.test', 'x')
        self.staff = User.objects.create_user('plain', 'p@a.test', 'x')

    def test_an_ordinary_account_cannot_see_or_change_the_wording(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get('/it/emails/').status_code, 403)
        self.assertEqual(
            self.client.post('/it/emails/kudos.received/save/',
                             {'subject': 'mine'}).status_code, 403)
        self.assertFalse(EmailTemplate.objects.exists())

    def test_saving_and_toggling_are_recorded_in_the_audit_log(self):
        from core.models import ITAuditLog
        self.client.force_login(self.admin)
        self.client.post('/it/emails/kudos.received/save/',
                         {'subject': 'Kudos from {actor}', 'enabled': '1'})
        self.client.post('/it/emails/kudos.received/toggle/', {})
        summaries = list(ITAuditLog.objects.values_list('summary', flat=True))
        self.assertTrue(any('Email changed' in s for s in summaries), summaries)
        self.assertTrue(any('switched off' in s for s in summaries), summaries)

    def test_an_attachment_the_case_does_not_offer_is_dropped(self):
        """Posting a value the case cannot produce must not leave a switch set
        that nothing will ever act on."""
        self.client.force_login(self.admin)
        self.client.post('/it/emails/kudos.received/save/',
                         {'subject': '', 'body': '', 'enabled': '1',
                          'attachment': 'athar_certificate'})
        self.assertEqual(
            EmailTemplate.objects.get(code='kudos.received').attachment, '')

    def test_the_case_that_does_offer_one_keeps_it(self):
        self.client.force_login(self.admin)
        self.client.post('/it/emails/athar.released/save/',
                         {'subject': '', 'body': '', 'enabled': '1',
                          'attachment': 'athar_certificate'})
        self.assertEqual(
            EmailTemplate.objects.get(code='athar.released').attachment,
            'athar_certificate')


class TheEditorShowsWhatIsBeingSent(TestCase):
    """The boxes carry the wording in force, so a small edit is a small edit.

    An empty box with the standard behind a placeholder attribute meant retyping
    a whole sentence to change one word in it, which is the opposite of the point
    of this screen.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser('root2', 'r2@a.test', 'x')
        self.client.force_login(self.admin)

    def test_the_boxes_arrive_carrying_the_standard_wording(self):
        found = CASES_BY_CODE['athar.released']
        subject, body = current_text('athar.released')
        self.assertEqual(subject, found.subject)
        self.assertEqual(body, found.body)

    def test_the_boxes_arrive_carrying_an_override_once_there_is_one(self):
        EmailTemplate.objects.create(code='athar.released',
                                     subject='Mine: {period}')
        subject, body = current_text('athar.released')
        self.assertEqual(subject, 'Mine: {period}')
        # The body was never overridden, so it still shows the standard rather
        # than coming back empty and inviting somebody to retype it.
        self.assertEqual(body, CASES_BY_CODE['athar.released'].body)

    def test_saving_the_standard_back_unchanged_keeps_it_following_the_standard(self):
        """Otherwise anybody who opened the editor to flip a switch would freeze
        today's wording into the database and stop receiving improvements to it."""
        found = CASES_BY_CODE['athar.released']
        self.client.post('/it/emails/athar.released/save/',
                         {'subject': found.subject, 'body': found.body,
                          'enabled': '1'})
        row = EmailTemplate.objects.get(code='athar.released')
        self.assertEqual(row.subject, '')
        self.assertEqual(row.body, '')
        self.assertFalse(row.is_customised)

    def test_a_one_word_change_is_stored_as_an_override(self):
        found = CASES_BY_CODE['athar.released']
        self.client.post('/it/emails/athar.released/save/',
                         {'subject': 'Your ' + found.subject,
                          'body': found.body, 'enabled': '1'})
        row = EmailTemplate.objects.get(code='athar.released')
        self.assertEqual(row.subject, 'Your ' + found.subject)
        self.assertEqual(row.body, '', 'the body was not touched, so it stays linked')

    def test_the_standard_retyped_with_a_dash_for_a_comma_is_still_unchanged(self):
        """The comparison runs after the dash rule on both sides, so using an em
        dash where the standard has a comma does not mark the case reworded. A
        dash where the standard has a colon is a real change, and does."""
        found = CASES_BY_CODE['athar.nomination_approved_winner']
        self.assertIn(', ', found.body)     # the comma the dash stands in for
        typed = found.body.replace(', ', ' \u2014 ', 1)
        self.client.post('/it/emails/athar.nomination_approved_winner/save/',
                         {'subject': found.subject, 'body': typed, 'enabled': '1'})
        row = EmailTemplate.objects.get(code='athar.nomination_approved_winner')
        self.assertEqual(row.body, '', row.body)

    def test_a_dash_in_a_genuine_override_is_stripped_on_the_way_in(self):
        self.client.post('/it/emails/kudos.received/save/',
                         {'subject': 'Kudos \u2014 well earned',
                          'body': 'Nice one \u2014 really.',
                          'enabled': '1'})
        row = EmailTemplate.objects.get(code='kudos.received')
        self.assertEqual(row.subject, 'Kudos, well earned')
        self.assertEqual(row.body, 'Nice one, really.')

    def test_the_preview_reads_as_a_real_email_not_as_braces(self):
        found = CASES_BY_CODE['athar.released']
        shape = resolve('athar.released', examples_for(found))
        self.assertEqual(shape.subject, 'The Athar Award: Q1 2026')
        self.assertNotIn('{', shape.subject)
        for line in shape.body_lines:
            self.assertNotIn('{', line, line)

    def test_every_placeholder_in_use_has_an_example_value(self):
        """A placeholder with no example shows as its own name in the preview,
        which is the thing this replaced."""
        from core.email_cases import EXAMPLES
        for c in CASES:
            for p in c.placeholders:
                self.assertIn(p, EXAMPLES, f'{c.code} uses {{{p}}} with no example')
