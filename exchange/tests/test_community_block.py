"""The "From your communities" block, and the two lists inside it.

Both read one `limit`: the chips capped at six and the threads at four off the
same number, so tuning either moved both and the thread cap could not be set on
its own. That is what let the card outgrow the recognition block beside it in the
same grid row — which then stretched to match and left a band of empty white
under its own last row.
"""
import logging

from django.contrib.auth.models import User
from django.test import RequestFactory, TestCase

from exchange.models import Community, Thread


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


class _Section:
    """Just enough of a CMS section for a provider to read its config."""

    def __init__(self, **cfg):
        self.config = cfg


class CommunityBlockTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('member', password='x')

    def setUp(self):
        self.communities = []
        for i in range(8):
            c = Community.objects.create(name='Community %d' % i,
                                         slug='community-%d' % i)
            c.members.add(self.user)
            self.communities.append(c)
        for i in range(9):
            Thread.objects.create(community=self.communities[i % 8],
                                  author=self.user,
                                  title='Question %d' % i, body='x')

    def block(self, section):
        from cms.providers import community
        request = RequestFactory().get('/')
        request.user = self.user
        return community(section, request)

    def test_threads_are_capped_at_three_by_default(self):
        """Three fits beside the recognition block; four did not."""
        self.assertEqual(len(self.block(_Section())['threads']), 3)

    def test_the_chip_limit_no_longer_moves_the_thread_count(self):
        """They shared one key, so raising one raised the other."""
        out = self.block(_Section(limit=8))
        self.assertEqual(len(out['communities']), 8)
        self.assertEqual(len(out['threads']), 3)

    def test_the_thread_count_has_its_own_key(self):
        out = self.block(_Section(threads=6))
        self.assertEqual(len(out['threads']), 6)
        # And the chips keep their own default rather than following it.
        self.assertEqual(len(out['communities']), 6)

    def test_the_newest_threads_are_the_ones_shown(self):
        out = self.block(_Section())
        newest = list(Thread.objects.order_by('-created_at')[:3])
        self.assertEqual([t.pk for t in out['threads']], [t.pk for t in newest])

    def test_only_the_reader_s_own_communities(self):
        """A block titled "From your communities" must mean it.

        The thread list used to take the newest threads across the whole Hub,
        so it showed questions from communities the reader had never joined —
        and on a private one, to somebody with no right to look.
        """
        outsider = Community.objects.create(name='Not mine', slug='not-mine')
        loud = Thread.objects.create(community=outsider, author=self.user,
                                     title='Should not appear', body='x')
        out = self.block(_Section())
        self.assertNotIn(loud.pk, [t.pk for t in out['threads']])
        self.assertNotIn(outsider.pk, [c.pk for c in out['communities']])

    def test_somebody_in_no_communities_sees_nothing(self):
        from cms.providers import community
        request = RequestFactory().get('/')
        request.user = User.objects.create_user('loner', password='x')
        out = community(_Section(), request)
        self.assertEqual(out['communities'], [])
        self.assertEqual(out['threads'], [])
