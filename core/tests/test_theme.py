"""The light/dark switch, and the parts of it that must not drift apart.

The theme is chosen in the browser and stored there, so nothing on the server
knows which one a reader is on. That makes two things load-bearing, and neither
shows up in a screenshot of whichever theme happens to be current:

  1. The inline bootstrap in <head> that writes data-theme before the first
     paint. Without it every page opens light and repaints, which is the flash
     the attribute exists to prevent — and no test of the rendered colours would
     notice it going missing.
  2. The control itself, in the user menu, with the ids the script binds to.

There is also one rule the whole palette rests on: yellow does not move between
the themes, so type sitting on yellow must not move either. Written as --ink it
became white text on a yellow tile in dark mode. That is asserted against the
stylesheet, because it is the sort of thing a later edit reintroduces by reflex.

The page is rendered through RequestFactory rather than self.client. The test
client instruments template rendering and copies each Context, which raises on
this Python/Django pair (`Context.__copy__` -> `super().__copy__()`), so any
client call that renders a template fails for reasons unrelated to the Hub.
"""
import os
import pathlib
import re

from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, TestCase

from core import views

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CSS = os.path.join(REPO, 'static', 'css', 'aji_hub.css')



#: A colour that follows the theme, or is outright light. None of these belong
#: on the brand yellow, which is the one ground that never changes.
_THEME_FG = re.compile(
    r'color:\s*(var\(--(?:ink|ink-2|ink-3|paper|paper-2|paper-3|raised-fg)\)'
    r'|#fff\b|#ffffff\b|white\b)')
_YELLOW_BG = re.compile(r'background(?:-color)?:\s*var\(--yellow(?:-2)?\)')


def yellow_offenders(text):
    """(selector, colour) for every block that puts theme-following type on yellow.

    Walks declaration blocks so a rule split across lines is still seen whole.
    Nested blocks (@media, @supports) carry no declarations of their own and are
    skipped; so are the inlined base64 @font-face bodies.
    """
    out, i = [], 0
    while True:
        open_at = text.find('{', i)
        if open_at == -1:
            return out
        close_at = text.find('}', open_at)
        if close_at == -1:
            return out
        body = text[open_at + 1:close_at]
        if '{' not in body and len(body) < 2000:
            if _YELLOW_BG.search(body) and _THEME_FG.search(body):
                selector = text[max(0, open_at - 140):open_at]
                selector = selector.split('}')[-1].split('*/')[-1].strip()
                out.append((selector[:70], _THEME_FG.search(body).group(0)))
        i = close_at + 1


#: A background given as a literal light colour. Not caught by the white guard
#: above, which uses \b so that #fff does not match #FFF6C4 — and #FFF6C4 was
#: exactly the bug: a pale yellow cell that stayed pale in dark mode while its
#: text followed the theme to near-white.
_LIGHT_BG = re.compile(r'background(?:-color)?:\s*([^;}]+)')
#: `var(--soft, #FAFAF7)` is not a hardcoded ground. The literal is only reached
#: if the token is undefined, and these tokens are defined in both themes — the
#: fallback is there for the case the palette is missing entirely. Stripping
#: var() before looking for a hex is what separates the four rules written that
#: way from the one that really did fade a chat pane to literal white.
_VAR_CALL = re.compile(r'var\(\s*--[\w-]+\s*(?:,[^()]*)?\)')
_HEX = re.compile(r'#([0-9a-fA-F]{3})\b|#([0-9a-fA-F]{6})\b')
_OWN_COLOUR = re.compile(r'(?<![-\w])color:')
#: Grounds with no text on them, so the theme cannot make anything unreadable:
#: a QR code's quiet zone, a switch knob, the bar over a presence dot, the
#: colour swatches in the calendar legend, and a preview that frames a page of
#: paper on purpose.
_PALE_ALLOWED = ('.hm-wa-qr', 'ns-sw-knob', 's-presenting', 'year-legend-dot',
                 'ad-stage iframe')


def _is_light(hex_digits):
    if len(hex_digits) == 3:
        hex_digits = ''.join(c * 2 for c in hex_digits)
    r = int(hex_digits[0:2], 16)
    g = int(hex_digits[2:4], 16)
    b = int(hex_digits[4:6], 16)
    return (r + g + b) / 3 > 190


def _blocks(text):
    """(selector, body) for every flat declaration block."""
    i = 0
    while True:
        open_at = text.find('{', i)
        if open_at == -1:
            return
        close_at = text.find('}', open_at)
        if close_at == -1:
            return
        body = text[open_at + 1:close_at]
        if '{' not in body and len(body) < 2000:
            selector = text[max(0, open_at - 200):open_at]
            selector = selector.split('}')[-1].split('*/')[-1].strip()
            yield selector.replace('\n', ' ')[-90:], body
        i = close_at + 1


def pale_offenders(text):
    """(selector, colour) for every block with a light, theme-locked ground and
    no colour of its own.

    Such a block is painted once and read twice: light in the light theme, still
    light in the dark one, with var(--ink) type on it that has gone near-white.
    Anything in a gradient or a color-mix counts — the rule that started this
    mixed the brand yellow with a literal #fff, so it never moved either.
    """
    out = []
    for selector, body in _blocks(text):
        if any(a in selector for a in _PALE_ALLOWED):
            continue
        if _OWN_COLOUR.search(body):
            continue
        for value in _LIGHT_BG.findall(body):
            previous = None
            while previous != value:
                previous = value
                value = _VAR_CALL.sub('', value)
            for short, long in _HEX.findall(value):
                digits = short or long
                if _is_light(digits):
                    out.append((selector, '#' + digits))
                    break
            else:
                continue
            break
    return out


class ThemeBootstrapTests(TestCase):
    """What a signed-in reader is actually served."""

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user('theme-tester', password='x' * 24)

    def homepage(self):
        request = RequestFactory().get('/')
        request.user = self.user
        request.session = SessionStore()
        request._messages = FallbackStorage(request)
        response = views.home(request)
        if hasattr(response, 'render'):
            response.render()
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_head_sets_the_theme_before_paint(self):
        html = self.homepage()
        self.assertIn("setAttribute('data-theme'", html)
        self.assertIn('aji-theme', html)
        # With nothing saved it follows the machine rather than assuming light.
        self.assertIn('prefers-color-scheme: dark', html)

    def test_the_switch_is_in_the_user_menu(self):
        html = self.homepage()
        self.assertIn('id="themeSwBtn"', html)
        self.assertIn('id="themeSw"', html)
        # role=switch, because it is a button that reports a state.
        self.assertIn('role="switch"', html)

    def test_both_logo_files_are_offered(self):
        """The bundled wordmark is black ink, so a dark bar needs the other file.

        This also covers the staticfiles manifest: the storage in use hashes and
        manifests every asset, so a file referenced by {% static %} but never
        collected raises rather than 404ing quietly. Adding logo-dark.png
        without re-running collectstatic took the whole page down.
        """
        html = self.homepage()
        self.assertIn('logo-on-light', html)
        self.assertIn('logo-on-dark', html)


class PaletteTests(TestCase):
    """Rules about the tokens themselves, read off the stylesheet."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(CSS, encoding='utf-8') as fh:
            cls.css = fh.read()
        # The inlined base64 @font-face lines are hundreds of kilobytes each and
        # contain every character sequence imaginable.
        cls.lines = [line for line in cls.css.split('\n') if len(line) < 2000]

    def test_dark_palette_exists(self):
        self.assertIn(':root[data-theme="dark"]', self.css)

    def test_nothing_puts_theme_coloured_type_on_yellow(self):
        """--ink flips to near-white between themes; yellow does not.

        Checked per declaration block, not per line. The line-based version of
        this test passed while eleven rules were broken, because a rule that
        sets its yellow on one line and its colour on the next never has both
        on the same line — which is how a white speech-bubble icon survived on
        a yellow tile through two rounds of "fixed".
        """
        offenders = [f'{sel} -> {bad}'
                     for sel, bad in yellow_offenders(self.css)]
        self.assertEqual(offenders, [],
                         'type on a yellow ground must use var(--on-yellow)')

    def test_every_role_is_defined_in_both_themes(self):
        """The tokens that must not simply follow --ink/--paper.

        Each of these exists because one surface breaks under a straight swap:
        --bed (a photograph's ground), --disc (initials circles), --raised (a
        lifted dark band), --on-yellow and --gold (type on and of the brand
        yellow), --sw-on, --glass, --chat-bg, --soft. If a later edit drops one
        from either block, everything that reads it silently falls back to
        whatever it inherits.
        """
        light = self.css[self.css.index(':root{'):self.css.index(':root[data-theme="dark"]')]
        dark = self.css[self.css.index(':root[data-theme="dark"]'):]
        # --gold was here and is gone: a deep olive-yellow for type that read as
        # mustard beside the brand's own yellow. Type is ink now.
        for token in ('--bed', '--disc', '--raised', '--raised-fg',
                      '--sw-on', '--glass', '--chat-bg', '--soft', '--yellow-soft',
                      # Three event types that were literal pale hexes with
                      # literal dark type: readable, but locked to one theme.
                      '--violet', '--violet-bg', '--sky', '--sky-bg',
                      '--clay', '--clay-bg'):
            self.assertIn(token + ':', light, f'{token} missing from the light palette')
            self.assertIn(token + ':', dark, f'{token} missing from the dark palette')

    def test_on_yellow_never_moves(self):
        """--on-yellow is the one token that must be the same in both themes.

        Yellow does not change between light and dark, so the type on it cannot
        either. Overriding it in the dark block would reintroduce exactly the
        white-on-yellow this token was added to remove.
        """
        light = self.css[self.css.index(':root{'):self.css.index(':root[data-theme="dark"]')]
        dark = self.css[self.css.index(':root[data-theme="dark"]'):]
        self.assertIn('--on-yellow:', light)
        self.assertNotIn('--on-yellow:', dark,
                         'type on yellow must not be re-themed')

    def test_the_login_page_states_its_theme(self):
        """It does not extend base.html, so nothing else sets the attribute.

        Without it every var(--paper-*) and var(--ink) in the shared stylesheet
        resolved to the light palette on a page that is dark by design. The
        shared `.field input{background:var(--paper-2)}` painted both sign-in
        fields solid white while the page's own rule set the text to #fff:
        white on white, and a typed username was visible only because of the
        yellow ::selection behind it.
        """
        source = pathlib.Path(REPO, 'templates', 'core', 'login.html').read_text(
            encoding='utf-8')
        self.assertIn('data-theme="dark"', source)

    def test_document_paper_is_not_rethemed(self):
        """A rendered document is paper, in both themes.

        The viewer's stage is dark either way and a PDF page already renders as a
        white page on it. If these flipped, the same form would look like two
        different documents depending on whether it was saved as .pdf or .docx —
        and a picture inside a .docx, whose own ink is black, would vanish.
        """
        light = self.css[self.css.index(':root{'):self.css.index(':root[data-theme="dark"]')]
        dark = self.css[self.css.index(':root[data-theme="dark"]'):]
        for token in ('--doc-paper:', '--doc-ink:', '--doc-line:',
                      '--doc-shade:', '--doc-hover:', '--doc-select:'):
            self.assertIn(token, light, f'{token} must be defined')
            self.assertNotIn(token, dark,
                             f'{token} is the document colour, not the page colour')

    def test_form_controls_are_given_a_colour(self):
        """Controls do not inherit colour, so an unstyled one is browser-black.

        This single rule was the largest source of unreadable text in dark mode:
        every button, chip and textarea that never set a colour of its own.
        """
        self.assertIn('button,input,select,textarea,optgroup{color:inherit}', self.css)

    def test_panels_do_not_hardcode_white(self):
        """A literal white panel stays white in dark mode.

        Three exceptions are deliberate, and named so a fourth has to be a
        decision rather than an accident: a QR code's quiet zone, a switch knob
        riding a coloured track, and the bar drawn over a status dot.
        """
        allowed = ('.hm-wa-qr', 'ns-sw-knob', 's-presenting')
        white = re.compile(r'background(?:-color)?:\s*(#fff\b|#ffffff\b|white\b)',
                           re.I)
        offenders = []
        for i, line in enumerate(self.lines):
            if not white.search(line):
                continue
            # The declaration may sit a few lines below its selector.
            near = ' '.join(self.lines[max(0, i - 4):i + 1])
            if not any(a in near for a in allowed):
                offenders.append(line.strip()[:120])
        self.assertEqual(offenders, [], 'a panel ground should be var(--paper-2)')


    def test_no_pale_theme_locked_panel_in_the_stylesheet(self):
        """A light ground written as a literal, with themed type on it.

        The gap that let a whole class of bug through. The white guard above
        uses \\b, so #fff does not match #FFF6C4 — and the offenders were never
        white: a pale yellow booking cell, and a meeting block whose ground was
        color-mix(var(--yellow), #fff). Both stayed light in dark mode while
        their text went near-white, which is text you cannot read at all.
        """
        offenders = [f'{sel} -> {bad}' for sel, bad in pale_offenders(self.css)]
        self.assertEqual(offenders, [],
                         'a light literal ground needs a themed token, or its '
                         'own colour')

class TemplateColourTests(TestCase):
    """The same rules, applied to the page-level <style> blocks in templates.

    These were missed the first time round: the sweep ran over aji_hub.css only,
    and 71 white panels plus 13 yellow rules sat in templates/ where nothing had
    looked. The IT portal, Global Settings, the manage screens and the org chart
    were all still painted for a light theme.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.files = []
        for path in sorted(pathlib.Path(REPO, 'templates').rglob('*.html')):
            cls.files.append((path.relative_to(pathlib.Path(REPO)),
                              path.read_text(encoding='utf-8', errors='replace')))

    def test_no_hardcoded_white_panels(self):
        """One exception: a document preview frames a page of paper."""
        white = re.compile(r'background(?:-color)?:\s*(#fff\b|#ffffff\b|white\b)', re.I)
        offenders = []
        for rel, text in self.files:
            for i, line in enumerate(text.split(chr(10)), 1):
                if white.search(line) and 'ad-stage iframe' not in line:
                    offenders.append(f'{rel}:{i}')
        self.assertEqual(offenders, [], 'use var(--paper-2) for a panel ground')


    def test_no_pale_theme_locked_panel(self):
        """The same rule over the page-level <style> blocks."""
        offenders = []
        for rel, text in self.files:
            for sel, bad in pale_offenders(text):
                offenders.append(f'{rel}: {sel} -> {bad}')
        self.assertEqual(offenders, [],
                         'a light literal ground needs a themed token, or its '
                         'own colour')

    def test_no_theme_coloured_type_on_yellow(self):
        """Same block-level rule as the stylesheet. See yellow_offenders()."""
        offenders = []
        for rel, text in self.files:
            for sel, bad in yellow_offenders(text):
                offenders.append(f'{rel}: {sel} -> {bad}')
        self.assertEqual(offenders, [], 'use var(--on-yellow) on a yellow ground')
