# core/mailer.py
"""One way out of the building for email.

The Hub already had a Microsoft Graph integration for sending mail. This puts an
SMTP route beside it and one function in front of both, so nothing else in the
codebase has to know or care which is switched on — and switching from Graph to
a mail relay is a change on the Global Settings page rather than in code.

Nothing is sent unless an Intranet Admin has enabled a transport. That is
deliberate: a half-configured Hub that silently mails real people is worse than
one that mails nobody.
"""
import logging
import re

from django.core.mail import EmailMultiAlternatives, get_connection
from django.utils.html import strip_tags

from .models import GlobalSetting

logger = logging.getLogger(__name__)

# GlobalSetting keys for the SMTP route.
KEY_SMTP_ENABLED = 'smtp_enabled'
KEY_SMTP_HOST = 'smtp_host'
KEY_SMTP_PORT = 'smtp_port'
KEY_SMTP_SECURITY = 'smtp_security'      # '', 'tls' or 'ssl'
KEY_SMTP_USER = 'smtp_user'
KEY_SMTP_PASSWORD = 'smtp_password'
KEY_SMTP_FROM = 'smtp_from'


def smtp_config():
    return {
        'enabled': GlobalSetting.get(KEY_SMTP_ENABLED) == '1',
        'host': GlobalSetting.get(KEY_SMTP_HOST),
        'port': GlobalSetting.get(KEY_SMTP_PORT) or '587',
        'security': GlobalSetting.get(KEY_SMTP_SECURITY) or 'tls',
        'user': GlobalSetting.get(KEY_SMTP_USER),
        'password': GlobalSetting.get(KEY_SMTP_PASSWORD),
        'from': GlobalSetting.get(KEY_SMTP_FROM),
    }


def smtp_is_configured(config=None):
    config = config or smtp_config()
    return bool(config['host'] and config['from'])


def active_transport():
    """'graph', 'smtp' or None. Graph wins if both are on — it is the one the
    Hub was set up with, and having two live routes is how you end up sending
    everything twice."""
    from . import msgraph
    graph = msgraph.get_config()
    if graph['enabled'] and msgraph.is_configured(graph):
        return 'graph'
    config = smtp_config()
    if config['enabled'] and smtp_is_configured(config):
        return 'smtp'
    return None


def transport_status():
    """(transport, human explanation) for the settings page and the digest log."""
    from . import msgraph
    graph = msgraph.get_config()
    config = smtp_config()
    if graph['enabled'] and not msgraph.is_configured(graph):
        return None, 'Microsoft Graph is switched on but not fully filled in.'
    if config['enabled'] and not smtp_is_configured(config):
        return None, 'SMTP is switched on but has no host or From address.'
    active = active_transport()
    if active == 'graph':
        return 'graph', (f"Sending as {graph['sender']} through Microsoft Graph."
                         + _link_warning())
    if active == 'smtp':
        return 'smtp', (f"Sending as {config['from']} through {config['host']}."
                        + _link_warning())
    return None, ('No email transport is switched on, so the Hub is not sending '
                  'any email. Turn on Microsoft Graph or SMTP below.')


#: Addresses that only mean anything on the machine running the Hub.
LOCAL_HOSTS = {'localhost', '127.0.0.1', '0.0.0.0', '::1', '[::1]'}


def _link_warning():
    """Said here because this line is what every page reports email health from.

    Two states to warn about, and both of them look healthy from the transport's
    side, which is why they need saying out loud.

    No site address: mail goes out to everybody and the button that takes them
    back to the Hub is dropped from every one of them, silently, because there is
    no absolute address to point it at.

    A local site address: worse, because it clears the first warning. Every email
    then carries a link to localhost, which resolves on the recipient's own
    machine rather than the server — so the link is not missing, it is dead, and
    dead in a way that only shows up in somebody else's inbox.
    """
    from urllib.parse import urlparse

    from .notify import site_url
    base = site_url()
    if not base:
        return (' The site address is blank, so every email is going out without '
                'its link back to the Hub — set it under Site address.')
    host = (urlparse(base).hostname or '').lower()
    if host in LOCAL_HOSTS or host.endswith('.local'):
        return (f' The site address is {base}, which only resolves on the machine '
                f'running the Hub — every link in every email is dead for whoever '
                f'receives it. Fine while testing; set it to the address staff '
                f'use before anybody else is emailed.')
    return ''


def _valid(address):
    return bool(address and re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', address.strip()))


def send(to, subject, html, text=None, attachments=None,
         high_importance=False):
    """Send one email. Returns (ok: bool, error: str).

    Never raises: a notification failing to send must not take down the thing
    that triggered it. Somebody posting a message should not see an error
    because a mail server is down.

    `attachments` is a list of (filename, bytes, mimetype). Kept as bytes in
    memory rather than as a path, because the one thing attached today is a
    certificate the Hub renders on the fly and never writes to disk.

    `high_importance` sets the flag Outlook reads. Both transports have to be
    told separately: Graph has a field on the message, SMTP has headers, and
    neither knows about the other.
    """
    recipients = [to] if isinstance(to, str) else list(to or [])
    recipients = [a.strip() for a in recipients if _valid(a)]
    if not recipients:
        return False, 'No valid recipient address.'

    transport = active_transport()
    if transport is None:
        return False, transport_status()[1]

    try:
        if transport == 'graph':
            from . import msgraph
            return msgraph.send_email(recipients, subject, html,
                                      attachments=attachments,
                                      high_importance=high_importance)

        config = smtp_config()
        connection = get_connection(
            backend='django.core.mail.backends.smtp.EmailBackend',
            host=config['host'], port=int(config['port'] or 587),
            username=config['user'] or None, password=config['password'] or None,
            use_tls=config['security'] == 'tls',
            use_ssl=config['security'] == 'ssl',
            timeout=20,
        )
        message = EmailMultiAlternatives(
            subject=subject, body=text or strip_tags(html),
            from_email=config['from'], to=recipients, connection=connection)
        message.attach_alternative(html, 'text/html')
        if high_importance:
            # Three headers rather than one: Outlook reads Importance, older
            # clients read X-Priority, and X-MSMail-Priority is what Outlook
            # itself writes. Setting one and not the others gets an email that
            # is urgent in some inboxes and ordinary in the rest.
            message.extra_headers['Importance'] = 'High'
            message.extra_headers['X-Priority'] = '1'
            message.extra_headers['X-MSMail-Priority'] = 'High'
        for name, content, mimetype in (attachments or []):
            message.attach(name, content, mimetype)
        message.send(fail_silently=False)
        return True, ''
    except Exception as exc:                      # noqa: BLE001 — see docstring
        logger.error('[mailer] %s send failed: %s', transport, exc)
        return False, f'{transport} send failed: {exc}'


def test_send(to, code='admin.test', values=None, template=None):
    """The "send me a test" button.

    On the settings page it sends the plain test email. From the Emails tab in
    the IT portal it sends whichever case is being edited, with the edit applied
    but not yet saved, so somebody can see their wording in a real inbox before
    committing it. That is the whole point of the button there: a preview in the
    browser cannot show you what Outlook does with it.
    """
    from .email_cases import resolve
    from .notify import absolute, render_email

    shape = resolve(code, values or {}, template=template)
    if shape is None:
        return False, f'No such email case: {code}'
    html = render_email(
        title=shape.subject, lines=shape.body_lines,
        link=absolute('/'), link_label=shape.link_label, footer=shape.footer)
    return send(to, shape.subject, html,
                high_importance=shape.high_importance)
