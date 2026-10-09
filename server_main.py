# -*- coding: utf-8 -*-
"""The Hub as a single executable.

Double-click AJiHub.exe, or run it from a command prompt, and it serves the
Hub on this machine's port 8000. No Python, no virtualenv, no pip.

WHAT IT DOES ON FIRST RUN

  1. Makes a folder called AJiHub-data beside the executable. Everything that
     changes lives there — the database, uploaded files, the signing key, the
     log — and nothing that changes lives inside the executable, which is
     read-only and gets replaced whenever a new version is built.
  2. Copies the database that shipped with it into that folder, if there is
     one and the folder is empty.
  3. Applies any migrations the database has not seen.
  4. Serves.

On every later run it skips straight to 3, so an upgrade is: stop it, swap
the exe, start it. The data folder is untouched.

WHY A DATA FOLDER RATHER THAN THE DATABASE INSIDE THE EXE

A one-file PyInstaller build unpacks itself to a temporary directory and
deletes it on exit. A database written there would be thrown away every time
the server stopped, and nobody would notice until the day they looked for a
month of bookings.

PORT AND ADDRESS

    AJiHub.exe                 serves on 0.0.0.0:8000
    AJiHub.exe --port 8080     a different port
    AJiHub.exe --host 127.0.0.1    this machine only
    AJiHub.exe --admin         make an administrator and exit
"""
import argparse
import os
import secrets
import shutil
import sys
import traceback


def base_dir():
    """Where the executable is. Not where it unpacked itself to."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def bundle_dir():
    """Where the read-only files are: the temp unpack when frozen."""
    return getattr(sys, '_MEIPASS', base_dir())


DATA = os.path.join(base_dir(), 'AJiHub-data')


def prepare():
    """Make the data folder, the key and the database ready before Django."""
    os.makedirs(DATA, exist_ok=True)
    os.makedirs(os.path.join(DATA, 'media'), exist_ok=True)

    # The signing key. Generated here on first run rather than shipped, so two
    # copies of the same executable do not share one, and so a key is never
    # sitting inside a file that gets emailed around.
    key_file = os.path.join(DATA, 'secret.key')
    if not os.path.exists(key_file):
        with open(key_file, 'w', encoding='utf-8') as fh:
            fh.write(secrets.token_urlsafe(64))
    with open(key_file, encoding='utf-8') as fh:
        os.environ['AJI_SECRET_KEY'] = fh.read().strip()

    # The database. A copy shipped inside the bundle is the starting point;
    # after that the one in the data folder is the only one that matters.
    live = os.path.join(DATA, 'db.sqlite3')
    if not os.path.exists(live):
        seed = os.path.join(bundle_dir(), 'db.sqlite3')
        if os.path.exists(seed):
            shutil.copy2(seed, live)
    os.environ['AJI_DB_PATH'] = live

    # Uploaded files — car pictures and anything added later — live beside the
    # database, not inside the executable.
    shipped = os.path.join(bundle_dir(), 'media')
    if os.path.isdir(shipped):
        for folder, _, files in os.walk(shipped):
            rel = os.path.relpath(folder, shipped)
            target = os.path.join(DATA, 'media', rel) if rel != '.' \
                else os.path.join(DATA, 'media')
            os.makedirs(target, exist_ok=True)
            for name in files:
                dest = os.path.join(target, name)
                if not os.path.exists(dest):
                    shutil.copy2(os.path.join(folder, name), dest)
    os.environ['AJI_MEDIA_ROOT'] = os.path.join(DATA, 'media')


def main():
    ap = argparse.ArgumentParser(description='AJi Hub')
    ap.add_argument('--host', default='0.0.0.0')
    ap.add_argument('--port', type=int, default=8000)
    ap.add_argument('--admin', action='store_true',
                    help='Create an administrator account and exit.')
    ap.add_argument('--reminders', action='store_true',
                    help='Send the fleet renewal reminders and exit. '
                         'Run this daily from Task Scheduler.')
    ap.add_argument('--command', default='',
                    help='Run any management command and exit, e.g. '
                         '--command "fleet_reminders --dry-run".')
    ap.add_argument('--no-migrate', action='store_true')
    args = ap.parse_args()

    prepare()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aji_hub.settings')

    import django
    django.setup()
    from django.core.management import call_command

    if not args.no_migrate:
        print('  checking the database...')
        call_command('migrate', interactive=False, verbosity=0)

    # The daily jobs. A packaged build has no manage.py, so anything that has
    # to run on a schedule has to be reachable from the executable itself or
    # it simply never runs — which is the same as not having written it.
    if args.reminders:
        call_command('fleet_reminders')
        return 0

    if args.command:
        parts = args.command.split()
        call_command(*parts)
        return 0

    if args.admin:
        from django.contrib.auth.models import User
        name = input('  username: ').strip()
        if User.objects.filter(username=name).exists():
            print('  that name is taken.')
            return 1
        import getpass
        pw = getpass.getpass('  password: ')
        if len(pw) < 8:
            print('  eight characters or more, please.')
            return 1
        User.objects.create_superuser(name, '', pw)
        print('  done. Sign in at /admin/ or on the front page.')
        return 0

    from django.core.wsgi import get_wsgi_application
    from waitress import serve

    app = get_wsgi_application()
    shown = '127.0.0.1' if args.host in ('0.0.0.0', '') else args.host
    print()
    print('  AJi Hub is running.')
    print('  On this machine      http://%s:%d/' % (shown, args.port))
    if args.host == '0.0.0.0':
        print('  On the network       http://<this-server-name>:%d/' % args.port)
    print('  Car booking          /services/car-booking/')
    print('  Data folder          %s' % DATA)
    print()
    print('  Close this window to stop it.')
    print()
    serve(app, host=args.host, port=args.port, threads=8,
          ident='AJi Hub', clear_untrusted_proxy_headers=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('\n  stopped.')
    except Exception:                                    # noqa: BLE001
        # A frozen app that dies silently is a support call nobody can answer,
        # so the reason goes on screen and into a file beside the executable.
        traceback.print_exc()
        try:
            with open(os.path.join(base_dir(), 'AJiHub-error.log'), 'a',
                      encoding='utf-8') as fh:
                traceback.print_exc(file=fh)
        except OSError:
            pass
        input('\n  Press Enter to close.')
        sys.exit(1)
