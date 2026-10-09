"""Standalone local development without company infrastructure.

Use DJANGO_SETTINGS_MODULE=aji_hub.settings_local. This module does not load
.env files; set environment variables in the shell before starting Django.
"""
import os
from .settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',  # noqa: F405
    }
}
ALLOWED_HOSTS = os.environ.get('AJI_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
AUTHENTICATION_BACKENDS = ['django.contrib.auth.backends.ModelBackend']
MIDDLEWARE = [m for m in MIDDLEWARE  # noqa: F405
              if m != 'core.iis_auth.IISRemoteUserMiddleware']
IIS_SSO = None
AJIBRAIN_API_BASE = os.environ.get('AJIBRAIN_API_BASE', 'http://127.0.0.1:8001')
AJIPROP_API_BASE = os.environ.get('AJIPROP_API_BASE', '')
STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'
