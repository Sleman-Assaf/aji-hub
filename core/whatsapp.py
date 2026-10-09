# core/whatsapp.py
"""Send WhatsApp messages from AJi Hub through a WhatsApp API provider.

Configured by the Intranet Admin group on the Global Settings page
(/settings/). Built to support multiple providers — the first supported
provider is WaSenderAPI (https://wasenderapi.com).

Usage anywhere in the system:
    from core.whatsapp import send_whatsapp
    ok, error = send_whatsapp('+962790000000', 'Your order is ready ✔')
"""
import logging

import requests

from .models import GlobalSetting

logger = logging.getLogger(__name__)

# GlobalSetting keys for this integration
KEY_ENABLED = 'whatsapp_enabled'
KEY_PROVIDER = 'whatsapp_provider'
KEY_COUNTRY_CODE = 'whatsapp_default_country_code'
# WaSenderAPI provider keys
KEY_WASENDER_API_KEY = 'wasender_api_key'
KEY_WASENDER_BASE_URL = 'wasender_base_url'

PROVIDERS = [
    ('wasender', 'WaSenderAPI (wasenderapi.com)'),
]

WASENDER_DEFAULT_BASE = 'https://wasenderapi.com/api'


def get_config():
    return {
        'enabled': GlobalSetting.get(KEY_ENABLED) == '1',
        'provider': GlobalSetting.get(KEY_PROVIDER) or 'wasender',
        'country_code': GlobalSetting.get(KEY_COUNTRY_CODE),
        'wasender_api_key': GlobalSetting.get(KEY_WASENDER_API_KEY),
        'wasender_base_url': GlobalSetting.get(KEY_WASENDER_BASE_URL) or WASENDER_DEFAULT_BASE,
    }


def is_configured(config=None):
    config = config or get_config()
    if config['provider'] == 'wasender':
        return bool(config['wasender_api_key'])
    return False


def normalize_number(number, config=None):
    """Return the number in international format, applying the default
    country code to local numbers (e.g. 0790000000 -> +962790000000)."""
    config = config or get_config()
    n = ''.join(ch for ch in str(number) if ch.isdigit() or ch == '+')
    if n.startswith('+'):
        return n
    if n.startswith('00'):
        return '+' + n[2:]
    cc = config['country_code'].lstrip('+').strip() if config['country_code'] else ''
    if cc:
        return '+' + cc + n.lstrip('0')
    return '+' + n


def _send_wasender(config, to, message):
    """WaSenderAPI: POST {base}/send-message with a Bearer API key."""
    url = config['wasender_base_url'].rstrip('/') + '/send-message'
    try:
        resp = requests.post(
            url,
            headers={'Authorization': f'Bearer {config["wasender_api_key"]}',
                     'Content-Type': 'application/json'},
            json={'to': to, 'text': message},
            timeout=20,
        )
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code in (200, 201) and data.get('success', True):
            return True, ''
        detail = data.get('message') or data.get('error') or resp.text[:200]
        logger.error(f'[whatsapp][wasender] send failed {resp.status_code}: {detail}')
        return False, f'WaSenderAPI error ({resp.status_code}): {detail}'
    except requests.RequestException as exc:
        logger.error(f'[whatsapp][wasender] network error: {exc}')
        return False, f'Network error reaching WaSenderAPI: {exc}'


def send_whatsapp(to, message):
    """Send a WhatsApp text message via the configured provider.
    `to` may be local (0790000000) or international (+962790000000).
    Returns (ok: bool, error: str)."""
    config = get_config()
    if not config['enabled']:
        return False, 'WhatsApp messaging is disabled in Global Settings.'
    if not is_configured(config):
        return False, 'WhatsApp messaging is not fully configured in Global Settings.'

    to = normalize_number(to, config)
    if config['provider'] == 'wasender':
        return _send_wasender(config, to, message)
    return False, f'Unknown WhatsApp provider "{config["provider"]}".'
