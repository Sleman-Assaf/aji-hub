"""The Recognition block on the homepage, and the Athar that leads it.

Nothing caps how many nominations HR may approve in a cycle, so co-recipients
are ordinary rather than an edge case. The lead is the quarter, not the person,
and it draws itself three ways depending on how many people won.
"""
import logging
from datetime import date

from django.contrib.auth.models import User
from django.test import TestCase

from taqdeer.models import AwardCycle, Nomination, Praise


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


class _Section:
    """Just enough of a CMS section for the provider to read its limit."""

    def __init__(self, limit=5):
        self.config = {'limit': limit}


class AtharLeadTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.hr = User.objects.create_user('hr-taq', password='x')
        cls.people = [
            User.objects.create_user('p%d' % i, password='x',
                                     first_name='Person', last_name=chr(65 + i))
            for i in range(7)
        ]

    def setUp(self):
        self.cycle = AwardCycle.objects.create(year=2026, quarter=3)

    def award(self, who, citation='Because of the work.', cycle=None):
        return Nomination.objects.create(
            cycle=cycle or self.cycle, nominated_by=self.hr, recipient=who,
            recipient_name=who.get_full_name(), citation=citation,
            status=Nomination.Status.APPROVED)

    def block(self, limit=5):
        from cms.providers import taqdeer
        return taqdeer(_Section(limit), None)

    # ── the three shapes ────────────────────────────────────────────────────

    def test_one_recipient_leads_alone_with_a_citation(self):
        self.award(self.people[0], 'Digital skills.')
        out = self.block()
        self.assertEqual(len(out['athar']), 1)
        self.assertIsNotNone(out['athar_solo'])
        self.assertEqual(out['athar_few'], [])
        self.assertEqual(out['athar_many'], [])
        self.assertEqual(out['athar_cycle'], self.cycle)

    def test_two_or_three_share_the_lead(self):
        for i in range(3):
            self.award(self.people[i])
        out = self.block()
        self.assertIsNone(out['athar_solo'])
        self.assertEqual(len(out['athar_few']), 3)
        self.assertEqual(out['athar_many'], [])

    def test_four_or_more_become_names(self):
        for i in range(6):
            self.award(self.people[i])
        out = self.block()
        self.assertIsNone(out['athar_solo'])
        self.assertEqual(out['athar_few'], [])
        self.assertEqual(len(out['athar_many']), 6)

    def test_nobody_yet_means_no_lead(self):
        Praise.objects.create(giver=self.hr, recipient=self.people[0],
                              subject='Thanks', message='Well done.')
        out = self.block()
        self.assertEqual(out['athar'], [])
        self.assertIsNone(out['athar_cycle'])
        self.assertIsNone(out['athar_solo'])

    # ── the block's height budget ───────────────────────────────────────────

    def test_the_lead_spends_part_of_the_row_budget(self):
        """The block budgets height, not records.

        An Athar lead is about as tall as two and a half rows. Left unaccounted
        for, the block ran two rows past the communities card beside it in the
        same grid row — which then stretched to match and left a band of empty
        white under its own last item.
        """
        self.award(self.people[0])
        for i in range(9):
            Praise.objects.create(giver=self.hr, recipient=self.people[i % 7],
                                  subject='Thanks %d' % i, message='x')
        out = self.block(limit=6)
        self.assertEqual(len(out['athar']), 1)
        self.assertEqual(len(out['items']), 4)

    def test_with_no_lead_the_rows_take_the_whole_budget(self):
        """Exactly what was asked for: the space goes to other recognitions."""
        for i in range(9):
            Praise.objects.create(giver=self.hr, recipient=self.people[i % 7],
                                  subject='Thanks %d' % i, message='x')
        out = self.block(limit=6)
        self.assertEqual(out['athar'], [])
        self.assertEqual(len(out['items']), 6)

    def test_a_tiny_budget_still_leaves_one_row(self):
        """Never zero rows: a lead with nothing under it reads as a broken block."""
        self.award(self.people[0])
        Praise.objects.create(giver=self.hr, recipient=self.people[1],
                              subject='Thanks', message='x')
        out = self.block(limit=1)
        self.assertEqual(len(out['items']), 1)

    def test_the_rows_kept_are_the_newest_ones(self):
        """Trimmed after the sort, not before."""
        self.award(self.people[0])
        for i in range(9):
            Praise.objects.create(giver=self.hr, recipient=self.people[i % 7],
                                  subject='Thanks %d' % i, message='x')
        out = self.block(limit=6)
        newest = list(Praise.objects.order_by('-created_at')[:4])
        self.assertEqual([r['obj'].pk for r in out['items']],
                         [p.pk for p in newest])

    # ── which quarter, and in what order ────────────────────────────────────

    def test_the_lead_is_the_newest_cycle_that_has_anybody(self):
        """Not the current calendar quarter.

        In Q4 the Q3 winners should still lead, with the label saying which
        quarter they won — emptying the block for three months between cycles
        would be tidier and worse.
        """
        old = AwardCycle.objects.create(year=2026, quarter=2)
        self.award(self.people[0], cycle=old)
        newer = self.award(self.people[1], cycle=self.cycle)
        out = self.block()
        self.assertEqual(out['athar_cycle'], self.cycle)
        self.assertEqual([a.pk for a in out['athar']], [newer.pk])

    def test_co_recipients_are_not_ranked(self):
        """Alphabetical within the cycle: they won the same award."""
        self.award(self.people[2])          # Person C
        self.award(self.people[0])          # Person A
        self.award(self.people[1])          # Person B
        out = self.block()
        self.assertEqual([a.recipient_name for a in out['athar_few']],
                         ['Person A', 'Person B', 'Person C'])

    def test_the_lead_is_not_repeated_in_the_rows_below(self):
        """It was the same award twice: once as the lead, once as a row."""
        for i in range(2):
            self.award(self.people[i])
        out = self.block()
        led = {a.pk for a in out['athar']}
        rows = {r['obj'].pk for r in out['items'] if r['kind'] == 'award'}
        self.assertFalse(led & rows)

    def test_an_older_cycle_still_appears_as_a_row(self):
        """Only this quarter's winners lead; last quarter's are ordinary rows."""
        old = AwardCycle.objects.create(year=2026, quarter=2)
        before = self.award(self.people[3], cycle=old)
        self.award(self.people[0], cycle=self.cycle)
        out = self.block()
        rows = [r['obj'].pk for r in out['items'] if r['kind'] == 'award']
        self.assertIn(before.pk, rows)


class RecognitionWordingTests(TestCase):
    """The verb is what tells a reader which of the three kinds a row is."""

    @classmethod
    def setUpTestData(cls):
        cls.hr = User.objects.create_user('hr-word', password='x')
        cls.hr.is_staff = True
        cls.hr.save(update_fields=['is_staff'])
        cls.who = User.objects.create_user('recipient', password='x',
                                           first_name='Lama', last_name='Haddad')

    def rendered(self):
        from django.template.loader import render_to_string
        from cms.providers import taqdeer
        ctx = taqdeer(_Section(), None)
        ctx['section'] = type('S', (), {'title': '', 'config': {}})()
        return render_to_string('cms/sections/taqdeer.html', ctx)

    def test_a_kudos_names_the_sender_sooner(self):
        Praise.objects.create(giver=self.hr, recipient=self.who,
                              subject='Site handover', message='Thank you.')
        html = self.rendered()
        self.assertIn('thanked by', html)
        self.assertNotIn('received Kudos from', html)

    def test_a_certification_is_certified_not_obtained(self):
        from taqdeer.models import Certification, TaqdeerSettings
        s = TaqdeerSettings.get()
        s.certification_on_homepage = True
        s.save()
        Certification.objects.create(employee=self.who, name='PMP',
                                     issuer='PMI', published=True,
                                     obtained_on=date(2026, 8, 1))
        html = self.rendered()
        self.assertIn('certified', html)
        # "obtained PMP" read as acquiring something unnamed, and with a badly
        # typed certification name the row became unreadable.
        self.assertNotIn('obtained', html)
