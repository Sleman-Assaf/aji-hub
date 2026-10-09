# core/notify.py
"""One place that decides who gets told what, and how.

Before this, seventeen places in the codebase created a Notification row
directly. That was fine while the bell was the only destination — but the moment
some of those should also raise a desktop pop-up or send an email, seventeen
copies of "should we?" is seventeen chances to get it wrong, and no way for
somebody to switch anything off.

So: callers say what happened. This decides what to do about it.

    from core.notify import notify, Event
    notify(user, Event.APPROVAL, 'Training request approved',
           body='Your FIDIC course was approved.', link='/training/')

Three channels:

* **in-app** — a Notification row, which the bell polls. What always happened.
* **browser** — no server work. The bell's poller raises a desktop pop-up for
  unread rows whose event the reader has allowed, so this is a flag on the row
  rather than a second delivery.
* **email** — sent through core.mailer, which is Graph or SMTP depending on what
  an Intranet Admin has switched on. Off unless configured, so a half-set-up Hub
  mails nobody.

Nothing here raises. A notification that cannot be delivered must not take down
the thing that caused it.
"""
import logging

from django.utils.html import escape

from .email_cases import no_dashes
from .models import (GlobalSetting, Notification, NotificationChannel,
                     NotificationEvent, NotificationPreference)

logger = logging.getLogger(__name__)

Event = NotificationEvent
Channel = NotificationChannel

#: Which Notification.Type to store for each event, so the bell keeps colouring
#: rows the way it always has.
_TYPE_FOR = {
    Event.CHAT: Notification.Type.INFO,
    Event.MENTION: Notification.Type.MENTION,
    Event.APPROVAL: Notification.Type.INFO,
    Event.GENERAL: Notification.Type.INFO,
}

KEY_SITE_URL = 'site_base_url'


def site_url():
    """Where the Hub lives, for links in emails.

    An email with a relative link is an email with a dead link, so this has to be
    set for email to be worth sending. Blank is better than a guess: a wrong
    absolute URL sends people somewhere that is not the Hub.
    """
    return (GlobalSetting.get(KEY_SITE_URL) or '').rstrip('/')


def absolute(link):
    if not link:
        return site_url() or ''
    if link.startswith(('http://', 'https://')):
        return link
    base = site_url()
    return f'{base}{link}' if base else ''


def notify(user, event, title, body='', link='', email_subject=None,
           email_html=None, actor=None, case=None, values=None,
           attachments=None):
    """Tell one person about one thing. Returns what was actually delivered.

    `case` names an entry in `core.email_cases`, which is what makes the email
    editable from the IT portal: its subject, its wording, whether it is sent at
    all, whether it goes out flagged high importance, and whether a file is
    attached all come from there rather than from this call.

    `values` fills the placeholders in that wording.

    The case governs the **email only**. The bell keeps the `title` and `body`
    passed here, because the two are not the same job: a bell row is a line of
    summary sitting among thirty others, a subject line is written to be read on
    its own in an inbox. Forcing one string to serve both is how the bell ends up
    reading "Q1 2026: you can put people forward for the Athar Award".

    `attachments` may be a list of (filename, bytes, mimetype) or a callable
    returning one. A callable is preferred: it is only invoked once the case is
    known to want an attachment, so a switched-off automation does not render a
    certificate nobody receives.
    """
    from .email_cases import resolve

    delivered = {'in_app': False, 'email': False, 'email_error': ''}
    if user is None or not getattr(user, 'is_authenticated', True):
        return delivered
    if actor is not None and getattr(actor, 'pk', None) == user.pk:
        return delivered              # nobody needs telling about their own doing

    resolved = resolve(case, values or {}) if case else None

    try:
        prefs = NotificationPreference.for_user(user)
    except Exception as exc:                                   # noqa: BLE001
        logger.error('[notify] could not read preferences for %s: %s', user, exc)
        prefs = None

    wants = (lambda channel: prefs.wants(event, channel)) if prefs else (
        lambda channel: channel == Channel.IN_APP)

    # Chat has its own in-app surface — the badge on the button and the panel,
    # fed by the chat's own poll. Writing a bell row per message as well would
    # bury every other kind of notification under a conversation.
    if wants(Channel.IN_APP) and event != Event.CHAT:
        try:
            Notification.objects.create(
                recipient=user, type=_TYPE_FOR.get(event, Notification.Type.INFO),
                title=title[:200], body=(body or '')[:500], link=link or '')
            delivered['in_app'] = True
        except Exception as exc:                               # noqa: BLE001
            logger.error('[notify] in-app failed for %s: %s', user, exc)

    # A case switched off in the IT portal sends no email. The bell row above
    # still goes out: switching off "tell them by email" is not the same as
    # switching off "tell them".
    if resolved is not None and not resolved.enabled:
        return delivered

    if wants(Channel.EMAIL) and (user.email or '').strip():
        from .mailer import send

        subject, html, files = email_subject, email_html, None
        important = False
        if resolved is not None:
            subject = resolved.subject
            important = resolved.high_importance
            if not html:
                html = render_email(
                    title=resolved.subject, lines=resolved.body_lines,
                    link=absolute(link), link_label=resolved.link_label,
                    footer=resolved.footer)
            if resolved.attachment and attachments is not None:
                files = attachments() if callable(attachments) else attachments

        if not html:
            html = render_email(
                title=title, lines=[body] if body else [],
                link=absolute(link), link_label='Open in AJi Hub')
        if not subject:
            subject = no_dashes(f'AJi Hub: {title}')

        ok, error = send(user.email, no_dashes(subject), html,
                         attachments=files, high_importance=important)
        delivered['email'] = ok
        delivered['email_error'] = error

    return delivered


def notify_many(users, event, title, **kwargs):
    """The same, for a handful of people. Returns how many got each channel."""
    tally = {'in_app': 0, 'email': 0}
    for user in users:
        out = notify(user, event, title, **kwargs)
        tally['in_app'] += bool(out['in_app'])
        tally['email'] += bool(out['email'])
    return tally


def wants_browser(user, event):
    """Whether a desktop pop-up is allowed for this event. Read by the bell."""
    try:
        return NotificationPreference.for_user(user).wants(event, Channel.BROWSER)
    except Exception:                                          # noqa: BLE001
        return False


# ── the email itself ─────────────────────────────────────────────────────

def render_email(title, lines=(), link='', link_label='Open in AJi Hub',
                 items=(), footer=''):
    """The Hub's one email layout.

    Tables and inline styles, not a stylesheet and not flexbox: Outlook renders
    with Word, which knows neither, and an email that arrives unstyled in the
    one client everybody here uses is not much of an email.

    Two things it does that are worth knowing about. The logo is a real image
    but Outlook blocks images by default, so its alt text is styled to read as
    the wordmark when the picture never arrives. And the first thing in the body
    is a hidden preheader — the line the inbox shows next to the subject, which
    otherwise would have been whatever the first sentence happened to be.
    """
    base = site_url()

    body = ''.join(
        f'<p style="margin:0 0 15px;font-size:15px;line-height:1.7;color:#3A3A33;'
        f'font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">'
        f'{escape(line)}</p>' for line in lines if line)

    listing = ''
    if items:
        rows = ''.join(
            '<tr><td style="padding:12px 14px;background:#FAFAF7;'
            'border-left:3px solid #FCD700;border-radius:0 8px 8px 0">'
            f'<div style="font-size:14.5px;font-weight:700;color:#2E2E29;'
            f'font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">'
            f'{escape(head)}</div>'
            + (f'<div style="font-size:13px;color:#7A7970;margin-top:4px;'
               f'font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">'
               f'{escape(sub)}</div>' if sub else '')
            + '</td></tr><tr><td style="height:8px;line-height:8px">&nbsp;</td></tr>'
            for head, sub in items)
        listing = (f'<table role="presentation" cellpadding="0" cellspacing="0" '
                   f'width="100%" style="margin:4px 0 14px">{rows}</table>')

    # A table rather than a padded <a>: Outlook drops padding on an inline
    # element, which turns the button into a coloured word.
    button = ''
    if link:
        button = f"""
     <table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 4px">
      <tr><td align="center" bgcolor="#FCD700" style="border-radius:10px">
        <a href="{escape(link)}" style="display:inline-block;padding:14px 30px;
           font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
           font-size:14.5px;font-weight:700;color:#2E2E29;text-decoration:none;
           border-radius:10px;letter-spacing:.01em">{escape(link_label)} &rarr;</a>
      </td></tr>
     </table>"""

    note = (f'<p style="margin:20px 0 0;font-size:12.5px;color:#7A7970;line-height:1.65;'
            f'font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">'
            f'{escape(footer)}</p>' if footer else '')

    # Styled alt text, so a blocked image still reads as the brand rather than
    # as a broken picture icon.
    if base:
        brand = (f'<img src="{base}/static/img/logo.png" width="132" height="67" alt="AJi Hub" '
                 f'style="display:block;border:0;width:132px;height:auto;'
                 f'font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
                 f'font-size:19px;font-weight:800;color:#2E2E29;letter-spacing:-.02em">')
    else:
        brand = ('<span style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;'
                 'font-size:19px;font-weight:800;color:#2E2E29;letter-spacing:-.02em">'
                 'AJi&nbsp;Hub</span>')

    settings_link = (f'<a href="{base}/notifications/settings/" '
                     f'style="color:#7A7970;text-decoration:underline">Notification settings</a>'
                     if base else 'Notification settings')

    # The inbox preview line. Hidden in the message itself, and padded so the
    # client does not follow it with the first words of the layout.
    preheader = escape(next((l for l in lines if l), title))[:120]

    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#F2F1EC;-webkit-text-size-adjust:100%">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;
            height:0;width:0">{preheader}&#8203;&nbsp;&#8203;&nbsp;&#8203;&nbsp;&#8203;&nbsp;
            &#8203;&nbsp;&#8203;&nbsp;&#8203;&nbsp;&#8203;&nbsp;&#8203;&nbsp;&#8203;&nbsp;</div>
<table role="presentation" cellpadding="0" cellspacing="0" width="100%"
       style="background:#F2F1EC;padding:32px 12px">
 <tr><td align="center">
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
         style="max-width:580px;background:#ffffff;border:1px solid #E8E7DE;
                border-radius:14px;overflow:hidden">

   <!-- the brand rule, the same one the Hub wears across its own header -->
   <tr><td style="height:5px;line-height:5px;background:#FCD700;font-size:0">&nbsp;</td></tr>

   <tr><td style="padding:26px 32px 0">{brand}</td></tr>

   <tr><td style="padding:20px 32px 30px">
     <h1 style="margin:0 0 14px;font-size:21px;line-height:1.35;color:#2E2E29;
                font-weight:800;letter-spacing:-.02em;
                font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
       {escape(title)}</h1>
     {body}{listing}{button}{note}
   </td></tr>
  </table>

  <table role="presentation" cellpadding="0" cellspacing="0" width="100%"
         style="max-width:580px">
   <tr><td align="center" style="padding:16px 24px 0">
     <p style="margin:0;font-size:11.5px;color:#8B8A80;line-height:1.7;
               font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif">
       Sent by AJi Hub &middot; Arabtech Jardaneh<br>
       Choose which of these you receive in {settings_link}.
     </p>
   </td></tr>
  </table>
 </td></tr>
</table>
</body></html>"""
