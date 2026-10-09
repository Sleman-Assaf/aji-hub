# portal/sharepoint.py
"""
Microsoft Graph API client for SharePoint document libraries.

Flow:
  1. Authenticate with client_credentials (app-only, no user login needed)
  2. Resolve site ID from hostname + path
  3. Find the drive (document library) by display name
  4. List all files recursively, return structured dicts

All calls are cached via Django's cache framework (file-based by default).
Set SHAREPOINT_CACHE_TTL in settings to control freshness (default 300s).

Configuration (settings.py):
    SHAREPOINT_TENANT_ID      = 'xxxx-xxxx-xxxx'
    SHAREPOINT_CLIENT_ID      = 'xxxx-xxxx-xxxx'
    SHAREPOINT_CLIENT_SECRET  = 'your-secret'
    SHAREPOINT_SITE_HOSTNAME  = 'ajigroup.sharepoint.com'
    SHAREPOINT_SITE_PATH      = '/sites/AJiHub'
    SHAREPOINT_POLICIES_LIBRARY = 'Policies & SOPs'
    SHAREPOINT_BRAND_LIBRARY    = 'Brand Files'
    SHAREPOINT_CACHE_TTL        = 300
"""

import logging
from datetime import datetime

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

GRAPH = 'https://graph.microsoft.com/v1.0'


# ── Token ─────────────────────────────────────────────────────────────────────

def _get_token():
    """Return a cached app-only access token."""
    cached = cache.get('sp_access_token')
    if cached:
        return cached

    tenant = getattr(settings, 'SHAREPOINT_TENANT_ID', '')
    if not tenant:
        raise SharePointNotConfigured('SHAREPOINT_TENANT_ID not set')

    url  = f'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'
    data = {
        'grant_type':    'client_credentials',
        'client_id':     getattr(settings, 'SHAREPOINT_CLIENT_ID', ''),
        'client_secret': getattr(settings, 'SHAREPOINT_CLIENT_SECRET', ''),
        'scope':         'https://graph.microsoft.com/.default',
    }
    resp = requests.post(url, data=data, timeout=15)
    resp.raise_for_status()
    token_data = resp.json()
    token = token_data['access_token']
    expires_in = int(token_data.get('expires_in', 3600)) - 60
    cache.set('sp_access_token', token, expires_in)
    return token


def _headers():
    return {'Authorization': f'Bearer {_get_token()}', 'Accept': 'application/json'}


# ── Site + Drive resolution ───────────────────────────────────────────────────

def _get_site_id():
    cached = cache.get('sp_site_id')
    if cached:
        return cached

    hostname = getattr(settings, 'SHAREPOINT_SITE_HOSTNAME', '')
    path     = getattr(settings, 'SHAREPOINT_SITE_PATH', '')
    url      = f'{GRAPH}/sites/{hostname}:{path}'
    resp     = requests.get(url, headers=_headers(), timeout=15)
    resp.raise_for_status()
    site_id = resp.json()['id']
    cache.set('sp_site_id', site_id, 86400)   # cache site ID for 24h
    return site_id


def _get_drive_id(library_name):
    cache_key = f'sp_drive_id_{library_name}'
    cached = cache.get(cache_key)
    if cached:
        return cached

    site_id = _get_site_id()
    url  = f'{GRAPH}/sites/{site_id}/drives'
    resp = requests.get(url, headers=_headers(), timeout=15)
    resp.raise_for_status()
    drives = resp.json().get('value', [])
    for d in drives:
        if d.get('name', '').lower() == library_name.lower():
            drive_id = d['id']
            cache.set(cache_key, drive_id, 86400)
            return drive_id
    raise SharePointError(f'Library "{library_name}" not found. Available: {[d["name"] for d in drives]}')


# ── File listing ──────────────────────────────────────────────────────────────

def _list_folder(drive_id, folder_id='root', path=''):
    """Recursively list all files under a folder, return flat list of file dicts."""
    url   = f'{GRAPH}/drives/{drive_id}/items/{folder_id}/children'
    items = []
    while url:
        resp = requests.get(url, headers=_headers(), timeout=20)
        resp.raise_for_status()
        data = resp.json()
        for item in data.get('value', []):
            if 'folder' in item:
                sub_path = f"{path}/{item['name']}" if path else item['name']
                items.extend(_list_folder(drive_id, item['id'], sub_path))
            elif 'file' in item:
                items.append(_make_file_dict(item, path))
        url = data.get('@odata.nextLink')
    return items


def _make_file_dict(item, folder_path=''):
    """Convert a Graph API file item to a clean dict for templates."""
    modified_raw = item.get('lastModifiedDateTime', '')
    try:
        modified = datetime.fromisoformat(modified_raw.replace('Z', '+00:00'))
    except Exception:
        modified = None

    return {
        'id':           item.get('id', ''),
        'name':         item.get('name', ''),
        'folder':       folder_path,
        'path':         f"{folder_path}/{item['name']}" if folder_path else item['name'],
        'size':         item.get('size', 0),
        'size_display': _format_size(item.get('size', 0)),
        'modified':     modified,
        'modified_str': modified.strftime('%d %b %Y') if modified else '',
        'url':          item.get('webUrl', ''),
        'download_url': item.get('@microsoft.graph.downloadUrl', ''),
        'ext':          item['name'].rsplit('.', 1)[-1].lower() if '.' in item['name'] else '',
        'mime':         item.get('file', {}).get('mimeType', ''),
    }


def _format_size(size_bytes):
    if size_bytes >= 1_048_576:
        return f'{size_bytes / 1_048_576:.1f} MB'
    if size_bytes >= 1024:
        return f'{size_bytes / 1024:.0f} KB'
    return f'{size_bytes} B'


# ── Public API ────────────────────────────────────────────────────────────────

class SharePointNotConfigured(Exception):
    pass


class SharePointError(Exception):
    pass


def get_library_files(library_name):
    """
    Return a flat list of file dicts from a SharePoint document library.
    Results are cached for SHAREPOINT_CACHE_TTL seconds.
    Raises SharePointNotConfigured if credentials are missing.
    Raises SharePointError on API failures.
    """
    ttl       = getattr(settings, 'SHAREPOINT_CACHE_TTL', 300)
    cache_key = f'sp_files_{library_name}'
    cached    = cache.get(cache_key)
    if cached is not None:
        return cached

    drive_id = _get_drive_id(library_name)
    files    = _list_folder(drive_id)
    files.sort(key=lambda f: (f['folder'], f['name'].lower()))
    cache.set(cache_key, files, ttl)
    return files


def get_policies_files():
    library = getattr(settings, 'SHAREPOINT_POLICIES_LIBRARY', 'Policies & SOPs')
    return get_library_files(library)


def get_brand_files():
    library = getattr(settings, 'SHAREPOINT_BRAND_LIBRARY', 'Brand Files')
    return get_library_files(library)


def clear_cache():
    """Force-clear all SharePoint caches (call after uploading new files)."""
    for key in ['sp_access_token', 'sp_site_id']:
        cache.delete(key)
    policies_lib = getattr(settings, 'SHAREPOINT_POLICIES_LIBRARY', 'Policies & SOPs')
    brand_lib    = getattr(settings, 'SHAREPOINT_BRAND_LIBRARY',    'Brand Files')
    for lib in [policies_lib, brand_lib]:
        cache.delete(f'sp_files_{lib}')
        cache.delete(f'sp_drive_id_{lib}')


def is_configured():
    """Return True if SharePoint credentials are set in settings."""
    return bool(
        getattr(settings, 'SHAREPOINT_TENANT_ID',     '') and
        getattr(settings, 'SHAREPOINT_CLIENT_ID',     '') and
        getattr(settings, 'SHAREPOINT_CLIENT_SECRET', '')
    )
