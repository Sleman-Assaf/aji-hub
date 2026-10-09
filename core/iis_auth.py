# core/iis_auth.py
"""Sign-in by IIS: the Hub trusts the gateway's word for who is asking.

With IIS in front as the reverse proxy, authentication moves out of Django
entirely. IIS challenges the browser with Windows Authentication
(Negotiate/Kerberos against Active Directory — the same directory the Hub's
LDAP login asks, but asked by the web server, invisibly), and forwards every
request with a header saying who it authenticated: X-Remote-User, holding
DOMAIN\\username. Nobody sees a login page; opening the Hub *is* logging in,
as the account they logged into Windows with.

Trusting a header is safe exactly as far as nobody else can write it, so two
locks hold this shut:

* The Waitress ports only listen on 127.0.0.1 — the only road to Django runs
  through IIS, and IIS overwrites X-Remote-User on every request, so a browser
  sending its own copy achieves nothing.
* A shared token, X-IIS-Proxy-Token, set by the IIS rewrite rule and checked
  here — so that even another process on the same machine (or a
  misconfigured second proxy) cannot impersonate the gateway. Requests whose
  token does not match are treated as carrying no identity at all: the header
  is dropped and the ordinary login page still works.

Configured by iis_sso.json beside manage.py (tools/iis/setup_iis_proxy.ps1
writes it), the same pattern as mssql.json: no settings edits per machine,
and deleting the file returns the Hub to its own login page.

    iis_sso.json:
    {
      "enabled": true,
      "proxy_token": "…",              // must equal the token in web.config
      "create_unknown_users": false,   // AD user without a Hub account: 403,
                                       // or create the account on the spot
      "header": "HTTP_X_REMOTE_USER"   // rarely changed
    }
"""
import hmac
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import RemoteUserBackend
from django.contrib.auth.middleware import PersistentRemoteUserMiddleware

logger = logging.getLogger(__name__)


def sso_conf():
    return getattr(settings, 'IIS_SSO', None) or {}


class IISRemoteUserMiddleware(PersistentRemoteUserMiddleware):
    """Reads the gateway's identity header — after checking it really is the
    gateway talking.

    The Persistent variant on purpose: once IIS has said who somebody is, the
    session stands even for requests that arrive without the header (health
    checks, odd clients), instead of logging people out mid-call.
    """

    @property
    def header(self):
        return sso_conf().get('header') or 'HTTP_X_REMOTE_USER'

    def process_request(self, request):
        conf = sso_conf()
        token = conf.get('proxy_token') or ''
        given = request.META.get('HTTP_X_IIS_PROXY_TOKEN', '')
        if token and not hmac.compare_digest(given, token):
            # Not the gateway. Whatever identity the request claims, it is
            # nobody's word but its own — dropped, and the request continues
            # anonymously toward the ordinary login page.
            request.META.pop(self.header, None)
            return
        super().process_request(request)


class IISRemoteUserBackend(RemoteUserBackend):
    """Turns DOMAIN\\username from IIS into the Hub account it belongs to.

    Matching is case-insensitive because Active Directory's names are and
    Django's are not, and the difference between M.Hasan and m.hasan should
    not read as two people. Accounts are looked up, not created, unless
    iis_sso.json says otherwise — the Hub's accounts come from the employee
    import, and a typo'd AD name silently creating a ghost account is worse
    than a 403 somebody reports.
    """

    def clean_username(self, username):
        # DOMAIN\user and user@domain both mean user.
        if '\\' in username:
            username = username.rsplit('\\', 1)[1]
        if '@' in username:
            username = username.split('@', 1)[0]
        return username.strip()

    def authenticate(self, request, remote_user):
        if not remote_user:
            return None
        User = get_user_model()
        username = self.clean_username(remote_user)
        try:
            user = User.objects.get(username__iexact=username)
        except User.MultipleObjectsReturned:
            # Two accounts differing only by case: take the exact spelling if
            # one matches, refuse rather than guess if none does.
            user = User.objects.filter(username=username).first()
            if user is None:
                logger.warning('[iis-sso] ambiguous username %r', username)
                return None
        except User.DoesNotExist:
            if not sso_conf().get('create_unknown_users'):
                logger.info('[iis-sso] %r authenticated by IIS but has no '
                            'Hub account', username)
                return None
            user = User.objects.create(username=username.lower())
            user.set_unusable_password()
            user.save(update_fields=['password'])
            logger.info('[iis-sso] created account for %r', username)
        return user if self.user_can_authenticate(user) else None
