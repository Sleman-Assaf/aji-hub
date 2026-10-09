"""News: creating an article, its pictures, and the homepage card.

Three things that were wrong rather than merely untested: creating an article
handed back a blank form instead of the article, several pictures stacked into a
column that buried the comments, and everything past the fourth story simply did
not exist on the homepage.
"""
import logging
from datetime import date, timedelta

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from content.models import NewsArticle, NewsAttachment


def setUpModule():
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


def a_png(name='shot.png'):
    """A one-pixel PNG, so `is_image` and the img tags have something real."""
    data = bytes.fromhex(
        '89504e470d0a1a0a0000000d494844520000000100000001080600000'
        '01f15c4890000000a49444154789c63000100000500010d0a2db400000'
        '00049454e44ae426082')
    return SimpleUploadedFile(name, data, content_type='image/png')


def form_tag(html, marker='id="nfForm"'):
    """The opening tag of the form carrying `marker`."""
    at = html.index(marker)
    return html[html.rindex('<form', 0, at):html.index('>', at) + 1]


class NewsCreateTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.hr = User.objects.create_user('hr-person', password='x')
        cls.hr.groups.add(Group.objects.create(name='HR'))

    def setUp(self):
        self.client.force_login(self.hr)

    def test_creating_an_article_lands_on_the_article(self):
        """It landed on a blank new-article form instead.

        The shared create/edit form carried data-save, so the in-place save
        script swallowed the view's redirect — and because the form has file
        inputs it took the reload branch, reloading /news/new/ and handing back
        an empty form. It looked as though the article had not saved at all.
        """
        res = self.client.post(reverse('news_create'), {
            'title': 'A brand new story', 'body': 'Something happened.',
            'published': 'on',
        }, follow=True)
        article = NewsArticle.objects.get(title='A brand new story')
        self.assertEqual(
            res.redirect_chain,
            [(reverse('news_detail', args=[article.slug]), 302)])
        self.assertContains(res, 'A brand new story')

    def test_only_an_existing_article_saves_in_place(self):
        """Editing stays where you are; creating has nowhere to stay."""
        blank = self.client.get(reverse('news_create')).content.decode()
        self.assertNotIn('data-save', form_tag(blank))

        article = NewsArticle.objects.create(
            title='Already here', slug='already-here', body='x',
            published=True, date=date.today())
        page = self.client.get(
            reverse('news_edit', args=[article.slug])).content.decode()
        self.assertIn('data-save', form_tag(page))


class NewsGalleryTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        cls.reader = User.objects.create_user('reader', password='x')

    def setUp(self):
        self.client.force_login(self.reader)
        self.article = NewsArticle.objects.create(
            title='With pictures', slug='with-pictures', body='Body.',
            published=True, date=date.today())

    def page(self):
        return self.client.get(
            reverse('news_detail', args=[self.article.slug])).content.decode()

    def test_pictures_and_files_are_separated_by_the_view(self):
        """The template filtered on a Python property twice and counted neither."""
        NewsAttachment.objects.create(article=self.article, file=a_png('one.png'))
        NewsAttachment.objects.create(article=self.article, file=a_png('two.png'))
        NewsAttachment.objects.create(
            article=self.article,
            file=SimpleUploadedFile('report.pdf', b'%PDF-1.4'))

        res = self.client.get(reverse('news_detail', args=[self.article.slug]))
        self.assertEqual(len(res.context['images']), 2)
        self.assertEqual(len(res.context['files']), 1)
        self.assertEqual(len(res.context['attachments']), 3)

    def test_several_pictures_become_a_gallery(self):
        for n in range(3):
            NewsAttachment.objects.create(article=self.article,
                                          file=a_png('p%d.png' % n))
        html = self.page()
        self.assertIn('carou shots', html)
        self.assertIn('3 pictures', html)
        # One slide and one thumbnail per picture.
        self.assertEqual(html.count('data-carou-slide>'), 3)
        self.assertEqual(html.count('data-gal-to='), 3)

    def test_one_picture_needs_no_thumbnail_strip(self):
        NewsAttachment.objects.create(article=self.article, file=a_png())
        html = self.page()
        self.assertIn('1 picture', html)
        # On the markup, not on the bare attribute name: the gallery's script
        # mentions [data-gal-thumbs] as a selector, so it is on every article
        # page whether a strip was drawn or not.
        self.assertNotIn('class="nd-gal-thumbs"', html)
        self.assertNotIn('data-gal-to=', html)

    def test_an_article_with_no_pictures_draws_no_gallery(self):
        NewsAttachment.objects.create(
            article=self.article,
            file=SimpleUploadedFile('only.pdf', b'%PDF-1.4'))
        html = self.page()
        self.assertNotIn('carou shots', html)
        # Storage may rename a colliding upload, so the extension is what is
        # dependable here — the point is that the file is offered, not renamed.
        self.assertIn('.pdf', html)
        self.assertEqual(len(self.client.get(
            reverse('news_detail', args=[self.article.slug])).context['files']), 1)

    def test_the_gallery_script_is_inside_the_content_block(self):
        """Anything after the block's end in a child template is discarded.

        The thumbnail-sync script was written there, so the strip rendered and
        then never followed the picture.
        """
        for n in range(2):
            NewsAttachment.objects.create(article=self.article,
                                          file=a_png('q%d.png' % n))
        self.assertIn('thumbs.addEventListener', self.page())


class HomepageNewsTests(TestCase):
    """The card is a lead, three under it, and a strip of what came before."""

    def setUp(self):
        today = date.today()
        for n in range(12):
            NewsArticle.objects.create(
                title='Story %d' % n, slug='story-%d' % n, body='x',
                published=True, date=today - timedelta(days=n))

    def provider(self, limit=4):
        from cms.providers import news_list

        class _Section:
            config = {'limit': limit}
        return news_list(_Section(), None)

    def test_the_card_holds_a_lead_and_the_strip(self):
        """The limit is the whole card: one lead plus the rest in the strip."""
        out = self.provider(limit=10)
        self.assertEqual([a.title for a in out['articles']],
                         ['Story %d' % n for n in range(10)])

    def test_the_block_limit_sizes_the_strip(self):
        out = self.provider(limit=4)
        self.assertEqual([a.title for a in out['articles']],
                         ['Story 0', 'Story 1', 'Story 2', 'Story 3'])

    def test_one_article_leaves_nothing_to_scroll(self):
        NewsArticle.objects.exclude(slug='story-0').delete()
        self.assertEqual(len(self.provider()['articles']), 1)
