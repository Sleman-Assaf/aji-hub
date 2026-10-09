# core/msgraph.py
"""Talk to Microsoft Graph — sending mail, and reading calendars.

Configured by the Intranet Admin group on the Global Settings page
(/settings/). Uses the OAuth2 client-credentials flow: the Azure app
registration needs the *application* permission `Mail.Send` (admin
consented), and emails are sent as the configured sender mailbox.

Usage anywhere in the system:
    from core.msgraph import send_email
    ok, error = send_email('someone@aji.com', 'Subject', '<p>HTML body</p>')

**Calendar.** The homepage's "Your workspace" card shows the signed-in person's
Outlook day. That reuses the app registration above and needs one more
application permission on it, `Calendars.Read`, admin consented. Because it is
the *application* reading rather than the person, there is no sign-in for a
user to complete and no per-user token to keep — but it also means the app can
read every mailbox in the tenant, so it is switched on separately from email
and stays off until somebody turns it on deliberately.

    from core.msgraph import calendar_for
    events, error = calendar_for('someone@aji.com', start, end)

Nothing here is load-bearing for a page: every call returns an error string
rather than raising, and the caller falls back to the Hub's own calendar.
"""
import datetime
import json
import logging

import requests
from django.core.cache import cache

from .models import GlobalSetting

logger = logging.getLogger(__name__)

# GlobalSetting keys for this integration
KEY_ENABLED = 'msgraph_enabled'
KEY_TENANT = 'msgraph_tenant_id'
KEY_CLIENT_ID = 'msgraph_client_id'
KEY_CLIENT_SECRET = 'msgraph_client_secret'
KEY_SENDER = 'msgraph_sender'
#: Calendar reading is a separate switch over the same app registration: it is a
#: broader permission than sending mail and should not arrive as a side effect of
#: turning email on.
KEY_CAL_ENABLED = 'msgraph_calendar_enabled'

TOKEN_URL = 'https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token'
SENDMAIL_URL = 'https://graph.microsoft.com/v1.0/users/{sender}/sendMail'
CALENDARVIEW_URL = 'https://graph.microsoft.com/v1.0/users/{upn}/calendarView'

#: A homepage waits on this, so it gets a short leash — a slow Microsoft is not
#: a reason for the Hub to hang, and the caller has local data to fall back to.
CALENDAR_TIMEOUT = 6
#: How long one person's day is reused for. Long enough that a reload is free,
#: short enough that a meeting accepted at 9:02 is on the page by 9:05.
CALENDAR_TTL = 180
#: A failure is cached too, and briefly — otherwise every homepage load retries a
#: Microsoft that is down and every one of them waits CALENDAR_TIMEOUT to find out.
CALENDAR_FAIL_TTL = 60


def get_config():
    return {
        'enabled': GlobalSetting.get(KEY_ENABLED) == '1',
        'tenant_id': GlobalSetting.get(KEY_TENANT),
        'client_id': GlobalSetting.get(KEY_CLIENT_ID),
        'client_secret': GlobalSetting.get(KEY_CLIENT_SECRET),
        'sender': GlobalSetting.get(KEY_SENDER),
    }


def is_configured(config=None):
    config = config or get_config()
    return all([config['tenant_id'], config['client_id'], config['client_secret'], config['sender']])


def _get_token(config):
    """An access token for the app, cached until shortly before it lapses.

    Sending an email is occasional and could afford to ask for a token every
    time. Drawing the homepage is not: without this, every visit by every person
    paid for a round trip to login.microsoftonline.com before the first calendar
    request even started. The token is the app's, not a user's, so one cached
    copy serves everybody.
    """
    key = f"msgraph:token:{config['tenant_id']}:{config['client_id']}"
    token = cache.get(key)
    if token:
        return token

    resp = requests.post(
        TOKEN_URL.format(tenant=config['tenant_id']),
        data={
            'client_id': config['client_id'],
            'client_secret': config['client_secret'],
            'scope': 'https://graph.microsoft.com/.default',
            'grant_type': 'client_credentials',
        },
        timeout=20,
    )
    data = resp.json()
    if resp.status_code != 200 or 'access_token' not in data:
        raise RuntimeError(data.get('error_description') or f'Token request failed ({resp.status_code}).')

    token = data['access_token']
    # Retired five minutes early. A token that expires in flight comes back as a
    # 401 on something a person is waiting for, and the margin costs nothing.
    try:
        ttl = int(data.get('expires_in') or 3600) - 300
    except (TypeError, ValueError):
        ttl = 3300
    cache.set(key, token, max(60, ttl))
    return token


def send_email(to, subject, html_body, cc=None, attachments=None,
               high_importance=False):
    """Send an email via Microsoft Graph. `to` and `cc` accept a string or a
    list of addresses. Returns (ok: bool, error: str).

    `attachments` is a list of (filename, bytes, mimetype); Graph wants each one
    base64 encoded inline on the message. This is the simple route and it caps
    out around 3 MB of attachment in total, which a certificate PDF is nowhere
    near. Anything larger needs an upload session, and if that day comes this is
    where it goes.
    """
    config = get_config()
    if not config['enabled']:
        return False, 'Microsoft Graph email is disabled in Global Settings.'
    if not is_configured(config):
        return False, 'Microsoft Graph email is not fully configured in Global Settings.'

    if isinstance(to, str):
        to = [to]
    cc = [cc] if isinstance(cc, str) else (cc or [])

    message = {
        'message': {
            'subject': subject,
            'body': {'contentType': 'HTML', 'content': html_body},
            'toRecipients': [{'emailAddress': {'address': a}} for a in to],
            'ccRecipients': [{'emailAddress': {'address': a}} for a in cc],
        },
        'saveToSentItems': True,
    }
    if high_importance:
        message['message']['importance'] = 'high'
    if attachments:
        import base64
        message['message']['attachments'] = [
            {
                '@odata.type': '#microsoft.graph.fileAttachment',
                'name': name,
                'contentType': mimetype,
                'contentBytes': base64.b64encode(content).decode('ascii'),
            }
            for name, content, mimetype in attachments
        ]
    try:
        token = _get_token(config)
        resp = requests.post(
            SENDMAIL_URL.format(sender=config['sender']),
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            data=json.dumps(message),
            timeout=20,
        )
        if resp.status_code == 202:
            return True, ''
        try:
            detail = resp.json().get('error', {}).get('message', resp.text[:200])
        except ValueError:
            detail = resp.text[:200]
        logger.error(f'[msgraph] sendMail failed {resp.status_code}: {detail}')
        return False, f'Graph sendMail failed ({resp.status_code}): {detail}'
    except requests.RequestException as exc:
        logger.error(f'[msgraph] network error: {exc}')
        return False, f'Network error reaching Microsoft: {exc}'
    except RuntimeError as exc:
        logger.error(f'[msgraph] auth error: {exc}')
        return False, f'Authentication failed: {exc}'


# ── Calendar ───────────────────────────────────────────────────────────────

def calendar_enabled():
    return GlobalSetting.get(KEY_CAL_ENABLED) == '1'


class CalendarEntry:
    """One Outlook event, shaped like `content.CalendarEvent`.

    The agenda template reads `.title`, `.time`, `.location` and `.time_range`
    and must not have to know whether the day came from Outlook or from the
    Hub's own calendar — that is the whole point of the fallback. `.time` is
    None for an all-day event, which is exactly what the template already tests
    for.
    """

    #: Defaults at class level so an entry unpickled from a cache written before
    #: `end` existed still answers for it, rather than raising part-way through
    #: rendering the homepage. The cache turns over in three minutes anyway.
    end = None

    def __init__(self, title, time=None, location='', all_day=False, end=None):
        self.title = title
        self.time = time
        self.end = end
        self.location = location
        self.all_day = all_day

    @property
    def time_range(self):
        """"11:45 – 12:45", matching content.CalendarEvent.time_range.

        Same name and same shape as the Hub's own event, so the agenda can print
        one line for both without asking which calendar it came from.
        """
        if self.all_day or not self.time:
            return 'All day' if self.all_day else ''
        out = self.time.strftime('%H:%M')
        # Only a sane end: a meeting Graph reports as finishing before it starts
        # would otherwise print "11:45 – 10:45".
        if self.end and self.end > self.time:
            out += ' – ' + self.end.strftime('%H:%M')
        return out

    def __repr__(self):
        return f'<CalendarEntry {self.time} {self.title!r}>'


def _parse_graph_datetime(raw):
    """Graph's dateTime string to a datetime, or None if it is unparseable.

    Graph sends seven fractional digits — `2026-08-04T09:15:00.0000000` — which
    is more precision than `fromisoformat` accepts, so the string is cut to
    microseconds first. An unreadable timestamp drops the event rather than the
    whole card.
    """
    if not raw:
        return None
    try:
        return datetime.datetime.fromisoformat(raw[:26])
    except (ValueError, TypeError):
        return None


def calendar_for(upn, start, end, tz='UTC', limit=10):
    """Somebody's Outlook events between two datetimes.

    Returns (entries, error). `entries` is None when the calendar could not be
    read for any reason — switched off, not configured, no mailbox, Microsoft
    unreachable, permission not granted — and the caller is expected to fall
    back rather than show an error on the homepage. An empty list is different:
    it means the day really is clear.
    """
    if not calendar_enabled():
        return None, 'Calendar sync is switched off in Global Settings.'
    config = get_config()
    if not is_configured(config):
        return None, 'Microsoft Graph is not fully configured in Global Settings.'
    if not upn:
        return None, 'No mailbox address for this user.'

    key = f'msgraph:cal:{upn}:{start.isoformat()}:{end.isoformat()}:{limit}'
    hit = cache.get(key)
    if hit is not None:
        return hit

    try:
        token = _get_token(config)
        resp = requests.get(
            CALENDARVIEW_URL.format(upn=upn),
            headers={
                'Authorization': f'Bearer {token}',
                # Without this Graph answers in UTC and a 09:15 standup appears
                # on the agenda at 06:15. Asking for the office's zone is more
                # reliable than converting afterwards, because Microsoft applies
                # the meeting's own recurrence and DST rules while doing it.
                'Prefer': f'outlook.timezone="{tz}"',
            },
            params={
                'startDateTime': start.isoformat(),
                'endDateTime': end.isoformat(),
                '$select': 'subject,start,end,location,isAllDay,isCancelled',
                '$orderby': 'start/dateTime',
                '$top': limit,
            },
            timeout=CALENDAR_TIMEOUT,
        )
        if resp.status_code != 200:
            try:
                detail = resp.json().get('error', {}).get('message', resp.text[:200])
            except ValueError:
                detail = resp.text[:200]
            logger.warning(f'[msgraph] calendarView {resp.status_code} for {upn}: {detail}')
            result = (None, f'Graph calendarView failed ({resp.status_code}): {detail}')
            cache.set(key, result, CALENDAR_FAIL_TTL)
            return result

        entries = []
        for item in resp.json().get('value', []):
            if item.get('isCancelled'):
                continue
            all_day = bool(item.get('isAllDay'))
            when = _parse_graph_datetime((item.get('start') or {}).get('dateTime'))
            if when is None and not all_day:
                continue
            # `end` was already being asked for in $select and then thrown away,
            # so the agenda could only ever say when a meeting started.
            finish = _parse_graph_datetime((item.get('end') or {}).get('dateTime'))
            entries.append(CalendarEntry(
                title=item.get('subject') or '(No subject)',
                time=None if all_day else when.time(),
                end=None if (all_day or finish is None) else finish.time(),
                location=(item.get('location') or {}).get('displayName') or '',
                all_day=all_day,
            ))

        result = (entries, '')
        cache.set(key, result, CALENDAR_TTL)
        return result

    except requests.RequestException as exc:
        logger.warning(f'[msgraph] calendar network error: {exc}')
        result = (None, f'Network error reaching Microsoft: {exc}')
    except RuntimeError as exc:
        logger.warning(f'[msgraph] calendar auth error: {exc}')
        result = (None, f'Authentication failed: {exc}')
    cache.set(key, result, CALENDAR_FAIL_TTL)
    return result
