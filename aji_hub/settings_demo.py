"""Settings for the demo instance the campaign screens are captured from.

Jordan goes live on 15 September and the live database holds 1,262 real people,
with their birthdays, their Kudos and their photographs. None of that is going
into a marketing film, so the demo content is seeded into a *copy* of the
database and served separately. This module is the whole isolation mechanism:
everything comes from the real settings, and only the database file changes.

    set DJANGO_SETTINGS_MODULE=aji_hub.settings_demo
    python manage.py seed_news_demo

MEDIA_ROOT is deliberately shared. The demo needs the real photographs to look
like the real Hub, and the seeders add a handful of files rather than rewriting
what is there. Delete db_demo.sqlite3 and this file when the campaign is shot.
"""
from .settings import *                                            # noqa: F401,F403
from .settings import BASE_DIR, DATABASES as _LIVE

DATABASES = {
    'default': {**_LIVE['default'], 'NAME': BASE_DIR / 'db_demo.sqlite3'},
}

# The demo instance is never reached from another machine, and never signs
# anything the live instance has to honour.
ALLOWED_HOSTS = ['127.0.0.1', 'localhost']

# Every screen worth filming is behind a login, and a headless browser cannot
# fill in a login form. So this instance — and only this instance — signs
# loopback requests in as a demo account. See aji_hub/demo_capture.py for what
# stops that from ever applying to the real Hub; the short version is that the
# module refuses to import unless the database is db_demo.sqlite3.
#
# It goes after AuthenticationMiddleware, because it needs request.user to
# exist before it can decide whether anyone is signed in.
MIDDLEWARE = list(MIDDLEWARE)                                      # noqa: F405
MIDDLEWARE.insert(
    MIDDLEWARE.index(
        'django.contrib.auth.middleware.AuthenticationMiddleware') + 1,
    'aji_hub.demo_capture.AutoLogin',
)
