"""Serve the Hub on this machine, for this office.

    python serve_local.py                 # http://<this machine>:8000
    python serve_local.py --port 8080
    python serve_local.py --debug          # keep Django's error pages on

`manage.py runserver` is a development server: single-threaded, and its own
documentation says not to use it for anything else. This runs the same project
through waitress, a real WSGI server, with the housekeeping a served instance
needs done first — migrations applied, static files collected, a real secret key
in place.

What it deliberately does NOT do:

  · terminate TLS. Traffic is plain http on the local network. core/certs.py
    exists for the certificate side of that and is a separate job.
  · put a web server in front. Uploaded files are served by Django itself,
    which is fine for one machine and a few hundred people and is not fine
    facing the internet.
  · check permissions on /media/. It never has — which is why a restricted
    document is never linked to its media URL; see portal/documents.py. On a
    laptop that was academic. On the network it means anyone who can reach this
    server can fetch any media path they can guess.

Stop it with Ctrl-C.
"""
import argparse
import os
import pathlib
import secrets
import socket
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
#: Generated once and kept out of the repository. A served instance signing
#: sessions with the key that is printed in settings.py means anybody holding a
#: copy of the source can mint a session cookie for it.
KEY_FILE = HERE / '.secret_key'


def secret_key():
    if KEY_FILE.exists():
        key = KEY_FILE.read_text(encoding='utf-8').strip()
        if key:
            return key
    key = secrets.token_urlsafe(64)
    KEY_FILE.write_text(key, encoding='utf-8')
    try:
        os.chmod(KEY_FILE, 0o600)
    except OSError:
        pass                                   # best effort; Windows ACLs differ
    print(f'  a new secret key was written to {KEY_FILE.name}')
    return key


def addresses():
    """Every name and address this machine can be reached at, best effort."""
    names = [socket.gethostname()]
    found = []
    for name in names:
        try:
            for info in socket.getaddrinfo(name, None, socket.AF_INET):
                ip = info[4][0]
                if ip not in found and not ip.startswith('127.'):
                    found.append(ip)
        except socket.gaierror:
            pass
    return names, found


def run(step, args, env):
    print(f'\n── {step}')
    done = subprocess.run([sys.executable, 'manage.py', *args],
                          cwd=HERE, env=env)
    if done.returncode:
        print(f'\n{step} failed. Nothing is serving.')
        sys.exit(done.returncode)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--port', type=int, default=8000)
    ap.add_argument('--host', default='0.0.0.0',
                    help='0.0.0.0 answers on every network card; 127.0.0.1 '
                         'keeps it on this machine only.')
    ap.add_argument('--threads', type=int, default=8)
    ap.add_argument('--debug', action='store_true',
                    help="Leave Django's debug pages on. Do not use for anything "
                         'other people will look at: a traceback page prints '
                         'settings and query values to whoever hit the error.')
    ap.add_argument('--skip-checks', action='store_true')
    opts = ap.parse_args()

    env = dict(os.environ)
    env['DJANGO_SETTINGS_MODULE'] = 'aji_hub.settings'
    env['AJI_DEBUG'] = '1' if opts.debug else '0'
    env['AJI_SECRET_KEY'] = secret_key()
    # Uploaded files come through Django, because Django's static() helper stops
    # serving them the moment DEBUG is off. See settings.SERVE_MEDIA.
    env['AJI_SERVE_MEDIA'] = '1'
    # Left wide on purpose: staff reach the Hub by machine name, by IP and by
    # whatever alias IT put in DNS, and this script cannot know which. Narrow it
    # with AJI_ALLOWED_HOSTS if you want to.
    env.setdefault('AJI_ALLOWED_HOSTS', '*')

    print(__doc__.splitlines()[0])
    print(f'  debug          : {"on — development only" if opts.debug else "off"}')

    if not opts.skip_checks:
        run('checking the project', ['check', '--deploy', '--fail-level', 'ERROR'], env)
    run('applying migrations', ['migrate', '--noinput'], env)
    run('collecting static files', ['collectstatic', '--noinput', '--clear'], env)

    try:
        from waitress import serve
    except ImportError:
        print('\nwaitress is not installed:  pip install waitress')
        sys.exit(1)

    os.environ.update(env)
    from aji_hub.wsgi import application

    names, ips = addresses()
    print('\n── serving')
    print(f'  this machine   : http://localhost:{opts.port}/')
    for name in names:
        print(f'  by name        : http://{name}:{opts.port}/')
    for ip in ips:
        print(f'  by address     : http://{ip}:{opts.port}/')
    if opts.host == '127.0.0.1':
        print('  (bound to 127.0.0.1 — nobody else on the network can reach it)')
    print('\n  Ctrl-C to stop.\n')

    serve(application, host=opts.host, port=opts.port, threads=opts.threads,
          ident='AJi Hub')


if __name__ == '__main__':
    main()
