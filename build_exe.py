# -*- coding: utf-8 -*-
"""Build AJiHub.exe — the whole Hub in one file, no Python needed to run it.

    .venv\\Scripts\\python.exe scratchpad\\build_exe.py

WHAT GOES IN

  · every app, its migrations, its management commands
  · every template and the collected static files
  · the car pictures (media/fleet) — the booking screen is a screen of cars
  · a copy of the database to start from

WHAT STAYS OUT

  · the rest of media/ — 3.6 GB of portraits and DAM assets
  · the virtualenv, __pycache__, the tile cache, backups, certificates

WHY MIGRATIONS NEED NAMING

PyInstaller follows imports. Django never imports a migration — it finds them
by walking the filesystem at runtime — so without listing them the exe builds
clean, starts, and then reports "no migrations to apply" against an empty
database. Every app's migrations package is collected explicitly below.
"""
import os
import shutil
import subprocess
import sys

ROOT = r'C:\Users\nael.shunnar\Downloads\Hub'
PY = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')
OUT = os.path.join(ROOT, 'dist')
WORK = os.path.join(ROOT, 'build')

APPS = ['core', 'people', 'content', 'exchange', 'portal', 'voice',
        'messaging', 'assist', 'services', 'cafeteria', 'cms', 'training',
        'taqdeer', 'dam', 'aji_hub']


def run(cmd, **kw):
    print('  $ ' + ' '.join(str(c) for c in cmd[:6]) + ' ...')
    return subprocess.run(cmd, check=True, cwd=ROOT, **kw)


def main():
    # ── 1. collect static, so the exe carries finished assets ─────────────
    print('\n  collecting static files')
    env = dict(os.environ, AJI_DEBUG='0')
    run([PY, 'manage.py', 'collectstatic', '--noinput', '--clear', '-v', '0'],
        env=env)

    # ── 2. work out what to carry ─────────────────────────────────────────
    data = [
        ('templates', 'templates'),
        ('staticfiles', 'staticfiles'),
        ('static', 'static'),
        (os.path.join('media', 'fleet'), os.path.join('media', 'fleet')),
        ('db.sqlite3', '.'),
    ]
    add_data = []
    for src, dest in data:
        full = os.path.join(ROOT, src)
        if os.path.exists(full):
            add_data += ['--add-data', '%s%s%s' % (full, os.pathsep, dest)]
        else:
            print('  (skipping %s — not there)' % src)

    hidden = []
    for app in APPS:
        hidden += ['--hidden-import', app]
        hidden += ['--hidden-import', '%s.apps' % app]
        # Django discovers migrations on disk, which PyInstaller cannot see.
        mig = os.path.join(ROOT, app, 'migrations')
        if os.path.isdir(mig):
            hidden += ['--collect-submodules', '%s.migrations' % app]
        for extra in ('models', 'admin', 'urls', 'views'):
            if os.path.exists(os.path.join(ROOT, app, extra + '.py')):
                hidden += ['--hidden-import', '%s.%s' % (app, extra)]
        cmds = os.path.join(ROOT, app, 'management', 'commands')
        if os.path.isdir(cmds):
            hidden += ['--collect-submodules', '%s.management.commands' % app]

    for mod in ('waitress', 'whitenoise', 'openpyxl', 'PIL', 'PIL.Image',
                'django.contrib.admin', 'django.contrib.messages',
                'django.contrib.staticfiles', 'django.template.loaders',
                'django.db.backends.sqlite3', 'django.core.mail.backends.smtp'):
        hidden += ['--hidden-import', mod]
    # Every one of these is reached by NAME at runtime — a middleware path in
    # settings, a storage backend, an image plugin — so PyInstaller's import
    # follower never sees them and they have to be collected whole.
    for pkg in ('django', 'whitenoise', 'waitress', 'openpyxl', 'PIL',
                'et_xmlfile'):
        hidden += ['--collect-all', pkg]

    excludes = []
    for mod in ('tkinter', 'matplotlib', 'numpy', 'scipy', 'PyInstaller',
                'pytest', 'IPython'):
        excludes += ['--exclude-module', mod]

    cmd = [PY, '-m', 'PyInstaller', '--noconfirm', '--onefile',
           '--name', 'AJiHub',
           '--distpath', OUT, '--workpath', WORK,
           '--specpath', WORK,
           '--console'] + add_data + hidden + excludes + [
        os.path.join(ROOT, 'server_main.py')]

    print('\n  building (this takes a few minutes)')
    run(cmd)

    exe = os.path.join(OUT, 'AJiHub.exe')
    print('\n  %s  %.1f MB' % (exe, os.path.getsize(exe) / 1048576))
    return exe


if __name__ == '__main__':
    main()
