# aji_hub/settings.py
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# ── what a deployment changes, and nothing else ──
# Read from the environment, defaulting to exactly what these were before, so
# `manage.py runserver` on a laptop behaves as it always has and a served
# deployment can differ without a second settings file to keep in step.
import os


def _flag(name, default):
    return os.environ.get(name, str(default)).strip().lower() in ('1', 'true', 'yes', 'on')


# Still the insecure default in development. serve_local.py generates a real one
# and keeps it in .secret_key, which is why that file must never be committed.
SECRET_KEY = os.environ.get(
    'AJI_SECRET_KEY', 'django-insecure-aji-hub-change-in-production')
DEBUG = _flag('AJI_DEBUG', True)
# '*' is the development default and is deliberate: the Hub is reached by
# machine name, by IP and by DNS alias, and a laptop cannot know which. A
# deployment can narrow it.
ALLOWED_HOSTS = [h.strip() for h in
                 os.environ.get('AJI_ALLOWED_HOSTS', '*').split(',') if h.strip()]

# Uploaded files. Django's static() helper below silently returns nothing when
# DEBUG is off, so with DEBUG=False every news photograph, brand asset and
# policy PDF served from /media/ answered 404 — the site looked intact and every
# picture was broken. Served explicitly instead, which is right for an internal
# Hub on one machine and is not a substitute for a web server in front of it if
# this ever leaves the building.
#
# Worth knowing, and unchanged by any of this: /media/ has never had a
# permission check. That is exactly why a restricted document is never linked to
# its media URL — see portal/documents.py — but it does mean anyone who can
# reach the server can fetch any media path they can guess.
SERVE_MEDIA = _flag('AJI_SERVE_MEDIA', True)

# Company mailbox addresses are the login name with the dot swapped for an
# underscore: munaf.dhuhairat -> munaf_dhuhairat@ajigroup.com. The HR feed
# uses this to fill empty email fields when AD carries no mail attribute.
# The aliases are wrong spellings found in old data (the dash form, and the
# no-i form); the feed and the HR import rewrite them to the real domain.
AJI_MAIL_DOMAIN = os.environ.get('AJI_MAIL_DOMAIN', 'ajigroup.com')
AJI_MAIL_DOMAIN_ALIASES = [d.strip().lower() for d in os.environ.get(
    'AJI_MAIL_DOMAIN_ALIASES', 'aj-group.com,ajgroup.com').split(',') if d.strip()]

# Extra names the https certificate has to cover, on top of the ones the machine
# reports for itself. See core/certs.py.
#
# The Hub's certificate is built from what the server calls itself — its computer
# name and the addresses its network cards hold. That is the whole story on a
# laptop. On a server it is not: staff open the Hub at whatever name IT put in
# DNS, and an alias is invisible to the machine it points at. A certificate that
# does not name the alias is rejected for it — the browser reports an invalid
# certificate, which reads like the certificate never took at all.
#
# So list here every name and address staff actually type, exactly as they type
# it. A name that is missing is a name the Hub cannot be opened on; a name listed
# that nobody uses costs nothing. The certificate is reissued automatically at
# the next start once this changes.
#
#   SSL_CERT_HOSTS = ['ajihub', 'hub.ajigroup.com', '172.30.0.4']
SSL_CERT_HOSTS = []

# ── the media bridge ──────────────────────────────────────────────────────────
# The media server that carries group calls past the 8-person mesh limit. Not
# enabled here: it is enabled by running it —
#
#   python manage.py run_media_bridge
#
# as its own process beside Waitress. ALL calls — direct and group — route
# through it while its health check answers, and fall back to the browser mesh
# while it does not. Direct calls ride the bridge for a networking reason:
# everyone can reach the server (they loaded the Hub from it), so a call
# carried here works across VLANs, isolated Wi-Fi and the VPN, where a
# browser-to-browser route often does not exist. `url` is the control API
# (offer/answer signalling, Django-to-bridge, localhost); the audio itself
# travels over the bridge's own UDP sockets directly to browsers.
# `max_participants` is the room size through the bridge — bounded by this
# server's CPU (mostly screen-share encoding), not by the clients.
# `carry_p2p` set to False keeps direct (two-person) calls browser-to-browser
# even while the bridge is up; group calls use the bridge regardless.
# `advertise_ip` — set on a multi-homed server (second NIC, Hyper-V or VPN
# adapters). Without it the bridge advertises every interface it has, the two
# ends of a connection can settle on different address pairs, and audio
# between subnets goes one-way. Set it to the one address every subnet
# reaches — the same one staff load the Hub from, e.g. '172.30.0.4' — and
# there is only one path left to agree on.
MEDIA_BRIDGE = {
    'url': 'http://127.0.0.1:8100',
    'max_participants': 64,
    'carry_p2p': True,
    # 'advertise_ip': '172.30.0.4',
}

# ── the call signalling trace ─────────────────────────────────────────────────
# Everything a call does through Django — starting, answering, declining,
# leaving, mode decisions (bridge or mesh), mute and share changes, offers
# proxied to the bridge — is logged by the `calls` logger, and this is what
# puts it in the Waitress terminal, timestamped, next to the bridge terminal
# showing the media side. Turn `calls` up to DEBUG to also see every note
# (offer/answer/ICE) carried between browsers in mesh mode.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'hub': {'format': '%(asctime)s.%(msecs)03d [%(name)s] '
                          '%(levelname)-7s %(message)s',
                'datefmt': '%H:%M:%S'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'hub'},
    },
    'loggers': {
        'calls': {'handlers': ['console'], 'level': 'INFO',
                  'propagate': False},
    },
}

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # AJi Hub apps
    'core',
    'people',
    'content',
    'exchange',
    'portal',
    'voice',
    'messaging',
    'assist',
    'services',
    'cafeteria',
    'cms',
    'training',
    'taqdeer',
    'dam',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.UpdateLastSeenMiddleware',
]

# The responsive preview shows a page inside an iframe at phone and tablet widths,
# because a media query keys off the viewport — narrowing a container would not
# make the mobile layout appear. Django's default of DENY blocks that even for our
# own origin.
#
# SAMEORIGIN keeps the protection that matters: another site still cannot frame the
# Hub, which is the clickjacking risk. Only our own pages can frame our own pages.
# Python's mimetypes table has no entry for .webmanifest, so Django's static
# serving guessed application/octet-stream and some browsers refuse a manifest on
# that type. A production web server needs the same mapping in its own config.
# WhiteNoise serves the static files here, and it builds its own mimetype table
# at startup rather than consulting Python's, so registering the type with
# mimetypes alone left the manifest going out as application/octet-stream and
# some browsers refuse a manifest on that type.
WHITENOISE_MIMETYPES = {'.webmanifest': 'application/manifest+json'}

X_FRAME_OPTIONS = 'SAMEORIGIN'


ROOT_URLCONF = 'aji_hub.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.hub_context',
                'services.context_processors.services_context',
                'cms.context_processors.cms_toolbar',
                'cms.context_processors.cms_breadcrumbs',
                'cms.context_processors.site_chrome',
            ],
        },
    },
]

WSGI_APPLICATION = 'aji_hub.wsgi.application'

# LDAP / Active Directory login (configured at /settings/ by Intranet Admins);
# falls through to Django's local accounts when disabled or on failure.
AUTHENTICATION_BACKENDS = [
    'core.ldap_auth.LDAPBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# ── IIS single sign-on ────────────────────────────────────────────────────────
# With IIS in front (tools/iis/setup_iis_proxy.ps1), authentication moves to
# the gateway: IIS does Windows Authentication against Active Directory and
# forwards the verified username in X-Remote-User, and the Hub signs people in
# as that account — no login page. Configured by iis_sso.json beside manage.py,
# the same pattern as mssql.json: the file is the switch, deleting it returns
# the Hub to its own login page. Format and the security model are documented
# in core/iis_auth.py.
IIS_SSO_CONFIG_FILE = BASE_DIR / 'iis_sso.json'
IIS_SSO = None
if IIS_SSO_CONFIG_FILE.exists():
    import json as _json
    try:
        IIS_SSO = _json.loads(
            IIS_SSO_CONFIG_FILE.read_text(encoding='utf-8-sig'))
    except (ValueError, OSError):
        IIS_SSO = None
    if not isinstance(IIS_SSO, dict):
        IIS_SSO = None

if IIS_SSO:
    # The file existing at all means the Hub sits behind the IIS gateway, and
    # these are gateway facts, not sign-on facts: the browser talks https to
    # the public name while Django sees plain http from 127.0.0.1, and links,
    # cookies and CSRF origin checks must be judged from the browser's side.
    # They therefore apply in BOTH auth modes — with enabled:false (workgroup
    # server, apps keep their own LDAP login page) exactly as with SSO;
    # tying them to `enabled` once made every login POST fail its origin
    # check the moment the gateway was in front.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    USE_X_FORWARDED_HOST = True
    if IIS_SSO.get('trusted_origins'):
        # Django insists every origin carries a scheme; a bare hostname in
        # iis_sso.json is normalized instead of crashing startup. Both forms
        # are trusted because the gateway answers on both ports — trim to
        # https:// only here once port 80 is retired.
        CSRF_TRUSTED_ORIGINS = []
        for _origin in IIS_SSO['trusted_origins']:
            _origin = str(_origin).strip().rstrip('/')
            if '://' in _origin:
                CSRF_TRUSTED_ORIGINS.append(_origin)
            elif _origin:
                CSRF_TRUSTED_ORIGINS += [f'https://{_origin}',
                                         f'http://{_origin}']

if IIS_SSO and IIS_SSO.get('enabled'):
    # Silent Windows sign-on — only meaningful when the gateway machine is
    # domain-joined and IIS authenticates. The middleware goes after
    # AuthenticationMiddleware, which RemoteUserMiddleware requires.
    MIDDLEWARE.insert(
        MIDDLEWARE.index(
            'django.contrib.auth.middleware.AuthenticationMiddleware') + 1,
        'core.iis_auth.IISRemoteUserMiddleware')
    AUTHENTICATION_BACKENDS.insert(0, 'core.iis_auth.IISRemoteUserBackend')

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# ── Microsoft SQL Server ─────────────────────────────────────────────────────
# Configured by a file rather than by editing this one: tools/install_mssql.ps1
# writes mssql.json beside manage.py, and flipping "enabled" is the whole
# switch-over. While enabled is false the server keeps running on SQLite and
# the MSSQL database is reachable as the 'mssql' alias, which is what
# `manage.py migrate_to_mssql` copies into. Once enabled, the roles swap:
# MSSQL is 'default' and the old SQLite file stays reachable as 'sqlite', so
# the migration can be re-run or checked against the original at any time.
#
#   mssql.json:
#   {
#     "enabled": false,
#     "host": "localhost\\SQLEXPRESS",   // or "server.aji.local", with
#     "port": "",                        // port "1433" for a remote server
#     "database": "ajihub",
#     "user": "ajihub",                  // empty user = Windows authentication
#     "password": "...",
#     "driver": "ODBC Driver 18 for SQL Server",
#     "trust_certificate": true          // Express uses a self-signed cert
#   }
import json as _json

MSSQL_CONFIG_FILE = BASE_DIR / 'mssql.json'
if MSSQL_CONFIG_FILE.exists():
    try:
        _mssql_cfg = _json.loads(MSSQL_CONFIG_FILE.read_text(encoding='utf-8-sig'))
    except (ValueError, OSError):
        _mssql_cfg = None
    if isinstance(_mssql_cfg, dict) and _mssql_cfg.get('host'):
        _extra = 'TrustServerCertificate=yes;' \
            if _mssql_cfg.get('trust_certificate', True) else ''
        if not _mssql_cfg.get('user'):
            _extra += 'Trusted_Connection=yes;'
        _MSSQL_DB = {
            'ENGINE': 'mssql',
            'NAME': _mssql_cfg.get('database', 'ajihub'),
            'HOST': _mssql_cfg.get('host', r'localhost\SQLEXPRESS'),
            'PORT': str(_mssql_cfg.get('port', '') or ''),
            'USER': _mssql_cfg.get('user', ''),
            'PASSWORD': _mssql_cfg.get('password', ''),
            'OPTIONS': {
                'driver': _mssql_cfg.get('driver',
                                         'ODBC Driver 18 for SQL Server'),
                'extra_params': _extra,
            },
        }
        _SQLITE_DB = {'ENGINE': 'django.db.backends.sqlite3',
                      'NAME': BASE_DIR / 'db.sqlite3'}
        if _mssql_cfg.get('enabled'):
            DATABASES = {'default': _MSSQL_DB, 'sqlite': _SQLITE_DB}
        else:
            DATABASES = {'default': _SQLITE_DB, 'mssql': _MSSQL_DB}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Amman'
USE_I18N = True
USE_TZ = False

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

LOGIN_URL = '/auth/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/auth/login/'

# AJiBrain integration (Ollama direct)
AJIBRAIN_API_BASE    = 'http://localhost:8001'
AJIBRAIN_ASSIST_MODEL = 'qwen2.5:14b'
AJIBRAIN_TIMEOUT     = 120
AJIBRAIN_TRUST_PROXY = False

# AJi-prop integration — RAG answers from the AJi-prop Knowledge Base
# Set to None or '' to disable (AJiAssist will use AJiBrain only)
AJIPROP_API_BASE    = 'http://172.30.0.4:8000'   # AJi-prop server address
AJIPROP_TIMEOUT     = 90                           # seconds
AJIPROP_TOP_K       = 3                            # KB chunks to retrieve
AJIPROP_TRUST_PROXY = False                        # set True if running behind corporate proxy

# Voice calls and screen sharing in chat. The Hub introduces two browsers to each
# other and then gets out of the way: the audio goes straight between them, and
# none of it passes through Django.
#
# Empty is the right setting for one office. Two machines on the AJi network find
# each other with the addresses their own network cards report, so no third party
# is needed and none of AJi's internal addressing is sent to one.
#
# It stops being right the moment somebody calls in from home or over the VPN.
# Two machines that cannot reach each other directly need a STUN server to
# discover how they look from outside, and — where even that fails, which is
# usual across two corporate firewalls — a TURN server to relay the audio.
# Both have to be reachable from the caller. Self-hosting coturn inside AJi and
# pointing this at it keeps the property that nothing leaves the network:
#
#   WEBRTC_ICE_SERVERS = [
#       {'urls': 'stun:turn.ajigroup.com:3478'},
#       {'urls': 'turn:turn.ajigroup.com:3478',
#        'username': '…', 'credential': '…'},
#   ]
WEBRTC_ICE_SERVERS = []

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── the map behind the car booking screens ───────────────────────────────
# OpenStreetMap's own tile servers are run by volunteers and they block any
# app that does not follow their usage policy — which is what a corporate
# intranet hammering them from one IP looks like. They answer 403 and the map
# fills with "Access blocked" tiles.
#
# CARTO's basemaps are built from the same OpenStreetMap data, are free to use
# at this scale, and do not require a key. Point this somewhere else if AJi
# ever runs its own tile server, or buys a MapTiler or Mapbox key:
#
#   MAP_TILE_URL = 'https://tiles.ajigroup.com/{z}/{x}/{y}.png'
#
# Nothing depends on the tiles being reachable. They are the picture behind
# the pins; the addresses are typed and stored as words, and a pin can be
# dropped on a blank grid, so a blocked or offline map costs the illustration
# and not the booking.
# Empty means "use the Hub's own tile proxy", which fetches from
# OpenStreetMap with a User-Agent that names AJi and caches every tile on disk
# — the two things their usage policy actually asks for. Set this to a full
# tile URL template to use a paid provider or a tile server inside AJi:
#
#   AJI_MAP_TILE_URL=https://api.maptiler.com/maps/streets/{z}/{x}/{y}.png?key=…
MAP_TILE_URL = os.environ.get('AJI_MAP_TILE_URL', '')
MAP_TILE_ATTRIBUTION = os.environ.get(
    'AJI_MAP_TILE_ATTRIBUTION', '&copy; OpenStreetMap contributors')
MAP_TILE_SUBDOMAINS = os.environ.get('AJI_MAP_TILE_SUBDOMAINS', 'abc')
#: Where a map opens when there is nothing to show yet. AJi head office.
MAP_HOME = (31.9539, 35.9106)

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
