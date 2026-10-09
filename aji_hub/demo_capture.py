"""Sign every request in, so the campaign screens can be captured headlessly.

WHY THIS EXISTS

The film is cut from real screens, and every screen worth showing is behind a
login. A headless browser cannot fill in a login form from the command line,
and Chromium's cookie store is DPAPI-encrypted on Windows, so a session cookie
cannot be planted into it either. The alternative was saving rendered HTML to
disk and screenshotting that, which loses everything the page fetches after
load — the org chart, the popovers, the counts.

So the demo instance signs the request in itself.

WHY IT IS SAFE HERE, AND ONLY HERE

This is an authentication bypass. It is confined by four things, and it should
stay that way:

  1. It is referenced only from settings_demo.py. The live settings never
     import it, so it cannot be switched on by an environment variable or a
     misplaced flag — someone would have to edit the live settings to reach it.
  2. It refuses to load unless the database is db_demo.sqlite3 — the copy
     holding 1,262 invented people. Pointed at the real database it raises at
     import and the process does not start.
  3. It only acts on requests from the loopback address.
  4. It signs in a named demo account, not a superuser, and never touches
     /admin/ — so a capture cannot wander into the admin and photograph
     something that is not part of the film.

Delete this file with db_demo.sqlite3 when the campaign is shot.
"""
from django.conf import settings
from django.contrib.auth import get_user_model, login

#: Guard 2. Read at import so a wrong database is a startup failure rather
#: than something discovered in a screenshot later.
_DB = str(settings.DATABASES['default']['NAME'])
if not _DB.endswith('db_demo.sqlite3'):
    raise RuntimeError(
        'demo_capture refuses to load: the database is %s, not the demo copy. '
        'This middleware signs every request in and must never be anywhere '
        'near the live database.' % _DB)

LOOPBACK = {'127.0.0.1', '::1'}

#: The account the screens are shot as. A real-looking demo person, chosen
#: once so every shot in the film is the same person's Hub — a film that
#: changes identity between cuts reads as a mock-up.
CAPTURE_USER = 'demo0001'


class AutoLogin:
    """Sign the request in as the capture user, if it is not already."""

    def __init__(self, get_response):
        self.get_response = get_response
        self._user = None

    def __call__(self, request):
        if (request.META.get('REMOTE_ADDR') in LOOPBACK
                and not request.path.startswith('/admin/')
                and not request.user.is_authenticated):
            user = self._capture_user()
            if user is not None:
                # backend named explicitly: there is no password to check and
                # authenticate() would refuse, which is the point.
                login(request, user,
                      backend='django.contrib.auth.backends.ModelBackend')
        return self.get_response(request)

    def _capture_user(self):
        if self._user is None:
            User = get_user_model()
            self._user = (User.objects.filter(username=CAPTURE_USER,
                                              is_active=True).first()
                          or User.objects.filter(is_active=True,
                                                 is_superuser=False)
                          .order_by('pk').first())
        return self._user
