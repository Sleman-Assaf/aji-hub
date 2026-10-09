# core/ldap_auth.py
"""LDAP / Microsoft Active Directory login for AJi Hub (on-prem domain
controllers). Configured by the Intranet Admin group on the Global Settings
page (/settings/).

How it authenticates:
1. Connects to the configured DC (ldap:// or ldaps://).
2. If a service (bind) account is configured, it binds with it and searches
   the Base DN for the user (default filter: sAMAccountName), then re-binds
   with the found user DN and the supplied password.
   If no service account is configured, it attempts a direct UPN bind
   (username@domain) instead.
3. On success, the matching Django user is fetched (or auto-created if
   enabled), and name/email are synced from AD attributes.

Local Django accounts keep working: this backend returns None on any failure
so authentication falls through to Django's ModelBackend.
"""
import logging

from django.contrib.auth.models import User

from .models import GlobalSetting

logger = logging.getLogger(__name__)

# GlobalSetting keys for this integration
KEY_ENABLED = 'ldap_enabled'
KEY_HOST = 'ldap_host'
KEY_PORT = 'ldap_port'
KEY_USE_SSL = 'ldap_use_ssl'
KEY_BIND_DN = 'ldap_bind_dn'
KEY_BIND_PASSWORD = 'ldap_bind_password'
KEY_BASE_DN = 'ldap_base_dn'
KEY_USER_FILTER = 'ldap_user_filter'
KEY_DOMAIN = 'ldap_domain'
KEY_AUTO_CREATE = 'ldap_auto_create'
KEY_ATTR_FIRST = 'ldap_attr_first'
KEY_ATTR_LAST = 'ldap_attr_last'
KEY_ATTR_EMAIL = 'ldap_attr_email'

DEFAULT_FILTER = '(sAMAccountName={username})'


def get_config():
    return {
        'enabled': GlobalSetting.get(KEY_ENABLED) == '1',
        'host': GlobalSetting.get(KEY_HOST),
        'port': GlobalSetting.get(KEY_PORT),
        'use_ssl': GlobalSetting.get(KEY_USE_SSL) == '1',
        'bind_dn': GlobalSetting.get(KEY_BIND_DN),
        'bind_password': GlobalSetting.get(KEY_BIND_PASSWORD),
        'base_dn': GlobalSetting.get(KEY_BASE_DN),
        'user_filter': GlobalSetting.get(KEY_USER_FILTER) or DEFAULT_FILTER,
        'domain': GlobalSetting.get(KEY_DOMAIN),
        'auto_create': GlobalSetting.get(KEY_AUTO_CREATE, '1') == '1',
        'attr_first': GlobalSetting.get(KEY_ATTR_FIRST) or 'givenName',
        'attr_last': GlobalSetting.get(KEY_ATTR_LAST) or 'sn',
        'attr_email': GlobalSetting.get(KEY_ATTR_EMAIL) or 'mail',
    }


def is_configured(config=None):
    config = config or get_config()
    return bool(config['host'] and config['base_dn'])


def _server(config):
    from ldap3 import Server
    port = int(config['port']) if str(config['port']).isdigit() else (636 if config['use_ssl'] else 389)
    return Server(config['host'], port=port, use_ssl=config['use_ssl'], connect_timeout=8)


def test_connection(config=None):
    """Bind with the service account (or anonymously) and check the Base DN.
    Returns (ok: bool, message: str)."""
    config = config or get_config()
    if not is_configured(config):
        return False, 'Server host and Base DN are required.'
    try:
        from ldap3 import Connection
        conn = Connection(
            _server(config),
            user=config['bind_dn'] or None,
            password=config['bind_password'] or None,
            receive_timeout=8,
        )
        if not conn.bind():
            return False, f'Bind failed: {conn.result.get("description")} — {conn.result.get("message", "")}'.strip()
        found = conn.search(config['base_dn'], '(objectClass=*)', search_scope='BASE', attributes=[])
        conn.unbind()
        if not found:
            return False, 'Connected, but the Base DN was not found. Check the Base DN.'
        return True, 'Connected and Base DN verified.'
    except ImportError:
        return False, "The 'ldap3' package is not installed on the server (pip install ldap3)."
    except Exception as exc:
        return False, f'Connection failed: {exc}'


def authenticate_ldap(username, password, config=None):
    """Core LDAP check. Returns (attrs_dict | None, message)."""
    config = config or get_config()
    if not (username and password):
        return None, 'Missing credentials.'
    try:
        from ldap3 import Connection
    except ImportError:
        logger.error("[ldap] ldap3 package not installed")
        return None, "The 'ldap3' package is not installed."

    server = _server(config)
    attrs = [config['attr_first'], config['attr_last'], config['attr_email']]

    try:
        if config['bind_dn']:
            # 1) service-account bind + search, 2) re-bind as the user
            conn = Connection(server, user=config['bind_dn'], password=config['bind_password'], receive_timeout=8)
            if not conn.bind():
                return None, f'Service account bind failed: {conn.result.get("description")}'
            flt = config['user_filter'].replace('{username}', username)
            if not conn.search(config['base_dn'], flt, attributes=attrs) or not conn.entries:
                conn.unbind()
                return None, 'User not found in the directory.'
            entry = conn.entries[0]
            user_dn = entry.entry_dn
            values = {a: (entry[a].value if a in entry else '') for a in attrs}
            conn.unbind()

            user_conn = Connection(server, user=user_dn, password=password, receive_timeout=8)
            if not user_conn.bind():
                return None, 'Invalid credentials.'
            user_conn.unbind()
            return values, 'OK'
        else:
            # direct UPN bind (username@domain), then read attributes as the user
            upn = username if '@' in username else (
                f'{username}@{config["domain"]}' if config['domain'] else username)
            conn = Connection(server, user=upn, password=password, receive_timeout=8)
            if not conn.bind():
                return None, 'Invalid credentials.'
            values = {}
            flt = config['user_filter'].replace('{username}', username.split('@')[0])
            if conn.search(config['base_dn'], flt, attributes=attrs) and conn.entries:
                entry = conn.entries[0]
                values = {a: (entry[a].value if a in entry else '') for a in attrs}
            conn.unbind()
            return values, 'OK'
    except Exception as exc:
        logger.error(f'[ldap] error authenticating {username}: {exc}')
        return None, f'LDAP error: {exc}'


class LDAPBackend:
    """Django authentication backend for the on-prem Active Directory."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        config = get_config()
        if not config['enabled'] or not is_configured(config):
            return None
        values, message = authenticate_ldap(username, password, config)
        if values is None:
            if message not in ('Invalid credentials.', 'User not found in the directory.'):
                logger.warning(f'[ldap] {message}')
            return None

        uname = username.split('@')[0].lower()
        try:
            user = User.objects.get(username__iexact=uname)
        except User.DoesNotExist:
            if not config['auto_create']:
                return None
            user = User(username=uname)
            user.set_unusable_password()  # AD is the source of truth

        # sync profile fields from AD on every login
        first = values.get(config['attr_first']) or user.first_name
        last = values.get(config['attr_last']) or user.last_name
        email = values.get(config['attr_email']) or user.email
        user.first_name = (first or '')[:150]
        user.last_name = (last or '')[:150]
        user.email = email or ''
        user.save()
        return user

    def get_user(self, user_id):
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None


def search_directory(query='', limit=200, config=None):
    """People in the directory, for the IT portal's import screen.

    Returns (ok, message, [{username, first, last, email, dn}]).

    Separate from authenticate_ldap, which binds as the person signing in. This
    binds as the service account and reads, so it needs bind_dn to be set — an
    anonymous bind is allowed by some servers and refused by most.

    Untested against a live directory: no LDAP server was reachable when this
    was written. It is written defensively for that reason — every failure is
    returned as a message rather than raised, so a misconfigured server shows a
    line of text on the page instead of a 500.
    """
    config = config or get_config()
    if not is_configured(config):
        return False, 'LDAP is not configured. Set the host and Base DN in Global Settings.', []
    try:
        from ldap3 import Connection, SUBTREE
    except ImportError:
        return False, "The 'ldap3' package is not installed on the server.", []

    attr_first = config['attr_first']
    attr_last = config['attr_last']
    attr_email = config['attr_email']
    base_filter = config['user_filter'] or DEFAULT_FILTER
    # The stored filter carries a {username} placeholder for the login lookup.
    # Here we want everybody, or a contains-match on the usual name fields.
    base_filter = base_filter.replace('{username}', '*')
    if query:
        safe = ''.join(ch for ch in query if ch.isalnum() or ch in ' .-_@')
        term = (f'(|(sAMAccountName=*{safe}*)(userPrincipalName=*{safe}*)'
                f'({attr_first}=*{safe}*)({attr_last}=*{safe}*)({attr_email}=*{safe}*))')
        search_filter = f'(&{base_filter}{term})'
    else:
        search_filter = base_filter

    try:
        conn = Connection(_server(config), user=config['bind_dn'] or None,
                          password=config['bind_password'] or None, receive_timeout=12)
        if not conn.bind():
            return False, f'Bind failed: {conn.result.get("description")}', []
        conn.search(config['base_dn'], search_filter, search_scope=SUBTREE,
                    attributes=['sAMAccountName', 'userPrincipalName',
                                attr_first, attr_last, attr_email],
                    size_limit=max(1, min(int(limit or 200), 1000)))
        people = []
        for entry in conn.entries:
            def val(name):
                try:
                    v = entry[name].value
                except Exception:
                    return ''
                if isinstance(v, (list, tuple)):
                    v = v[0] if v else ''
                return str(v or '').strip()
            username = val('sAMAccountName') or val('userPrincipalName').split('@')[0]
            if not username:
                continue
            people.append({
                'username': username.lower(),
                'first': val(attr_first),
                'last': val(attr_last),
                'email': val(attr_email) or val('userPrincipalName'),
                'dn': str(entry.entry_dn),
            })
        conn.unbind()
        people.sort(key=lambda p: (p['first'].lower(), p['last'].lower(), p['username']))
        return True, f'{len(people)} found.', people
    except Exception as exc:
        return False, f'Directory search failed: {exc}', []
