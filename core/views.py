# core/views.py
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.db.models import Q
from django.core.paginator import Paginator
from django.utils import timezone
from datetime import date

from people.models import Employee
from content.models import NewsArticle, CalendarEvent, Project
from exchange.models import Community, Thread
from django.contrib import messages

from .models import Notification, NotificationPreference, is_intranet_admin


@login_required
def notification_settings(request):
    """What this person is told about, and how.

    A row per kind of event, a column per channel. Everything is opt-out rather
    than opt-in, except emailing every chat message, which nobody wants and which
    the daily summary replaces.
    """
    from .mailer import test_send, transport_status
    from .models import NotificationChannel, NotificationEvent
    from .notify import site_url

    prefs = NotificationPreference.for_user(request.user)
    transport, transport_note = transport_status()

    if request.method == 'POST':
        if request.POST.get('action') == 'test_email':
            to = (request.user.email or '').strip()
            if not to:
                messages.error(
                    request,
                    'Your account has no email address on it, so there is nowhere '
                    'to send a test. Ask IT to add one.')
            elif transport is None:
                messages.error(request, transport_note)
            else:
                ok, error = test_send(to, code='admin.test')
                if ok:
                    messages.success(request, f'Test email sent to {to}.')
                else:
                    messages.error(request, f'Could not send: {error}')
            return redirect('notification_settings')

        for event in NotificationPreference.SWITCHED:
            for channel in NotificationChannel.values:
                field = f'{event}_{channel}'
                setattr(prefs, field, request.POST.get(field) == 'on')
        prefs.digest_email = request.POST.get('digest_email') == 'on'
        try:
            hour = int(request.POST.get('digest_hour', 17))
        except (TypeError, ValueError):
            hour = 17
        prefs.digest_hour = min(max(hour, 0), 23)
        prefs.save()
        messages.success(request, 'Notification settings saved.')
        return redirect('notification_settings')

    return render(request, 'core/notification_settings.html', {
        'prefs': prefs,
        'matrix': prefs.matrix(),
        'channels': NotificationChannel.choices,
        'channel_help': {
            'in_app': 'The bell at the top of every page.',
            'browser': 'A pop-up on your desktop while a Hub tab is open.',
            'email': 'A message to your inbox.',
        },
        'event_help': {
            'chat': 'Someone messages you, or posts in one of your groups. '
                    '“In the Hub” means the badge on the chat button.',
            'mention': 'Someone writes your @name in an Exchange thread or a '
                       'comment.',
            'approval': 'Something is waiting on you — a training request, a '
                        'service request, a cafeteria order — or something you '
                        'submitted has been decided.',
        },
        'hours': [(h, f'{h:02d}:00') for h in range(24)],
        'transport': transport,
        'transport_note': transport_note,
        'site_url': site_url(),
        'is_intranet_admin': is_intranet_admin(request.user),
    })


@login_required
def home(request):
    today = date.today()
    today_str = today.strftime('%m-%d')

    # Birthday employees today
    birthday_employees = Employee.objects.filter(
        birthday=today_str, is_active=True
    ).select_related('entity')[:3]

    # The side-by-side home cards show at most this many rows each so they end
    # up the same height; the remainder becomes a "+N more" link.
    HOME_ROWS = 3

    # Upcoming events — the "Official events" card, which looks ahead.
    upcoming_qs = CalendarEvent.objects.filter(date__gte=today).order_by('date', 'time')
    upcoming_events = list(upcoming_qs[:HOME_ROWS])
    upcoming_more = max(0, upcoming_qs.count() - HOME_ROWS)

    # Today's agenda — strictly events happening *today*, which includes a
    # multi-day event whose span covers today. This card previously reused
    # upcoming_events, so next week's meetings showed up under "today".
    today_qs = CalendarEvent.objects.filter(
        Q(date=today) | Q(date__lte=today, end_date__gte=today)
    ).order_by('time', 'date')
    today_events = list(today_qs[:HOME_ROWS])
    today_more = max(0, today_qs.count() - HOME_ROWS)

    # Latest news
    news_qs = NewsArticle.objects.filter(published=True).order_by('-date')
    latest_news = list(news_qs[:HOME_ROWS])
    news_more = max(0, news_qs.count() - HOME_ROWS)

    # Featured projects
    featured_projects = Project.objects.order_by('-year')[:12]

    # Communities with thread counts
    communities = Community.objects.filter(members=request.user)[:6]

    # Latest threads across all communities
    latest_threads = Thread.objects.select_related('community', 'author').order_by('-created_at')[:4]

    context = {
        'birthday_employees': birthday_employees,
        'upcoming_events': upcoming_events,
        'upcoming_more': upcoming_more,
        'today_events': today_events,
        'today_more': today_more,
        'latest_news': latest_news,
        'news_more': news_more,
        'featured_projects': featured_projects,
        'communities': communities,
        'latest_threads': latest_threads,
        'today': today,
    }
    return render(request, 'core/home.html', context)


@login_required
def search(request):
    """The top bar's search, across everything this reader may open.

    The sources live in core/search.py, one function each — see that module for
    what is searched, what is deliberately not, and why.
    """
    from .search import run
    results, suggestion = run(request.GET.get('q', ''), request.user)
    return JsonResponse({'results': results, 'suggestion': suggestion})


@login_required
def notifications_api(request):
    """The bell's contents, plus whether each row may raise a desktop pop-up.

    The decision belongs on the server: the browser has no idea which of these a
    person asked to be interrupted by, and asking it to guess from the link would
    put the rule in two places.
    """
    from .models import NotificationChannel, NotificationPreference

    notifs = list(Notification.objects.filter(recipient=request.user, is_read=False)[:20])
    prefs = NotificationPreference.for_user(request.user)
    allowed = {
        event: prefs.wants(event, NotificationChannel.BROWSER)
        for event in NotificationPreference.SWITCHED
    }

    data = [{
        'id': n.pk,
        'type': n.type,
        'title': n.title,
        'body': n.body,
        'link': n.link,
        'created_at': n.created_at.strftime('%d %b'),
        # A mention is its own event; everything else in the bell rides on the
        # approvals switch, which is what "something waiting on you" means.
        'popup': allowed['mention'] if n.type == Notification.Type.MENTION
                 else allowed['approval'],
    } for n in notifs]
    return JsonResponse({'notifications': data, 'unread': len(notifs)})


@login_required
def notifications_mark_read(request):
    if request.method == 'POST':
        Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    return JsonResponse({'ok': True})


@login_required
def notification_mark_one_read(request, pk):
    if request.method == 'POST':
        Notification.objects.filter(pk=pk, recipient=request.user, is_read=False).update(is_read=True)
    unread = Notification.objects.filter(recipient=request.user, is_read=False).count()
    return JsonResponse({'ok': True, 'unread': unread})


# Maps a notification's link prefix to a human-readable "service" name, so
# alerts can be filtered by where they came from without a schema change.
NOTIFICATION_SERVICES = [
    ('/services/job-requisition/', 'Job Requisition'),
    ('/services/approvals/', 'Approvals'),
    ('/news/', 'News'),
    ('/exchange/', 'Exchange'),
    ('/messages/', 'Messaging'),
    ('/messaging/', 'Messaging'),
    ('/people/', 'People & Culture'),
    ('/portal/', 'Portal'),
    ('/brand/', 'Brand'),
]


def _service_for_link(link):
    for prefix, label in NOTIFICATION_SERVICES:
        if link and link.startswith(prefix):
            return label
    return 'General'


@login_required
def alerts_history(request):
    q = request.GET.get('q', '').strip()
    ntype = request.GET.get('type', '').strip()
    service = request.GET.get('service', '').strip()
    date_from = request.GET.get('from', '').strip()
    date_to = request.GET.get('to', '').strip()

    notifs = Notification.objects.filter(recipient=request.user)

    if q:
        notifs = notifs.filter(Q(title__icontains=q) | Q(body__icontains=q))
    if ntype:
        notifs = notifs.filter(type=ntype)
    if date_from:
        notifs = notifs.filter(created_at__date__gte=date_from)
    if date_to:
        notifs = notifs.filter(created_at__date__lte=date_to)

    notifs = list(notifs)
    for n in notifs:
        n.service = _service_for_link(n.link)
    if service:
        notifs = [n for n in notifs if n.service == service]

    paginator = Paginator(notifs, 20)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, 'core/alerts_history.html', {
        'page_obj': page_obj,
        'q': q,
        'ntype': ntype,
        'service': service,
        'date_from': date_from,
        'date_to': date_to,
        'type_choices': Notification.Type.choices,
        'service_choices': sorted({label for _, label in NOTIFICATION_SERVICES}),
    })


# ─────────────────────────── Global Settings (Intranet Admin) ───────────────────────────

@login_required
def global_settings(request):
    """System-wide settings, editable by the Intranet Admin group.
    First section: Microsoft Graph API email integration. More sections
    will be added over time."""
    from django.contrib import messages
    from django.contrib.auth.models import Group
    from django.core.exceptions import PermissionDenied

    from .models import (GlobalSetting, INTRANET_ADMIN_GROUP, IT_GROUP,
                         user_can_manage_settings)
    from . import msgraph
    from . import ldap_auth
    from . import ldap_sync as ldap_sync_module
    from . import whatsapp

    # Both created on demand. The Policies group was gated on for months without
    # existing, which 404'd every screen behind it for everyone.
    Group.objects.get_or_create(name=INTRANET_ADMIN_GROUP)
    Group.objects.get_or_create(name=IT_GROUP)
    if not user_can_manage_settings(request.user):
        raise PermissionDenied

    if request.method == 'POST':
        section = request.POST.get('section')

        if section == 'msgraph':
            GlobalSetting.set(msgraph.KEY_ENABLED, '1' if request.POST.get('enabled') else '0', request.user)
            GlobalSetting.set(msgraph.KEY_TENANT, (request.POST.get('tenant_id') or '').strip(), request.user)
            GlobalSetting.set(msgraph.KEY_CLIENT_ID, (request.POST.get('client_id') or '').strip(), request.user)
            GlobalSetting.set(msgraph.KEY_SENDER, (request.POST.get('sender') or '').strip(), request.user)
            # keep the stored secret when the field is left blank
            secret = (request.POST.get('client_secret') or '').strip()
            if secret:
                GlobalSetting.set(msgraph.KEY_CLIENT_SECRET, secret, request.user)
            messages.success(request, 'Microsoft Graph email settings saved.')
            return redirect('it_integrations')

        if section == 'msgraph_calendar':
            GlobalSetting.set(msgraph.KEY_CAL_ENABLED,
                              '1' if request.POST.get('enabled') else '0', request.user)
            if request.POST.get('enabled'):
                messages.success(
                    request, 'Outlook calendar sync switched on. "Your workspace" '
                             'will show Outlook for anybody whose Hub account has '
                             'their email address on it.')
            else:
                messages.success(
                    request, 'Outlook calendar sync switched off. "Your workspace" '
                             'falls back to the Hub calendar.')
            return redirect('it_integrations')

        if section == 'msgraph_calendar_test':
            to = (request.POST.get('test_upn') or '').strip() or (request.user.email or '')
            if not to:
                messages.error(request, 'Enter a mailbox address to read.')
            else:
                import datetime as _dt
                from django.conf import settings as dj_settings
                start = _dt.datetime.combine(_dt.date.today(), _dt.time.min)
                entries, error = msgraph.calendar_for(
                    to, start, start + _dt.timedelta(days=1),
                    tz=getattr(dj_settings, 'TIME_ZONE', 'UTC') or 'UTC')
                if entries is None:
                    messages.error(request, f'Could not read {to}: {error}')
                elif not entries:
                    messages.success(
                        request, f'Read {to} successfully — nothing in the calendar today. '
                                 f'The permission is working.')
                else:
                    first = entries[0]
                    when = first.time.strftime('%H:%M') if first.time else 'All day'
                    messages.success(
                        request, f'Read {to} successfully — {len(entries)} event'
                                 f'{"" if len(entries) == 1 else "s"} today, '
                                 f'starting {when} “{first.title}”.')
            return redirect('it_integrations')

        if section == 'msgraph_test':
            to = (request.POST.get('test_to') or '').strip() or (request.user.email or '')
            if not to:
                messages.error(request, 'Enter a recipient address for the test email.')
            else:
                ok, error = msgraph.send_email(
                    to, 'AJi Hub — Microsoft Graph test email',
                    '<p>This is a test email sent from <b>AJi Hub</b> via the Microsoft Graph API. '
                    'If you received it, the integration is working. ✔</p>',
                )
                if ok:
                    messages.success(request, f'Test email sent to {to}. Check the inbox.')
                else:
                    messages.error(request, f'Test failed: {error}')
            return redirect('it_integrations')

        if section == 'smtp':
            from . import mailer
            GlobalSetting.set(mailer.KEY_SMTP_ENABLED,
                              '1' if request.POST.get('enabled') else '0', request.user)
            for key, field in [(mailer.KEY_SMTP_HOST, 'host'),
                               (mailer.KEY_SMTP_PORT, 'port'),
                               (mailer.KEY_SMTP_USER, 'user'),
                               (mailer.KEY_SMTP_FROM, 'from_address')]:
                GlobalSetting.set(key, (request.POST.get(field) or '').strip(),
                                  request.user)
            security = request.POST.get('security')
            GlobalSetting.set(mailer.KEY_SMTP_SECURITY,
                              security if security in ('', 'tls', 'ssl') else 'tls',
                              request.user)
            # Blank means "leave the stored one alone", so the password is not
            # wiped by somebody saving the rest of the form.
            password = request.POST.get('password') or ''
            if password.strip():
                GlobalSetting.set(mailer.KEY_SMTP_PASSWORD, password.strip(),
                                  request.user)
            messages.success(request, 'SMTP settings saved.')
            return redirect('it_integrations')

        if section == 'smtp_test':
            from .mailer import test_send
            to = (request.POST.get('test_to') or '').strip() or (request.user.email or '')
            if not to:
                messages.error(request, 'Enter a recipient address for the test.')
            else:
                ok, error = test_send(to, code='admin.test')
                if ok:
                    messages.success(request, f'Test email sent to {to}.')
                else:
                    messages.error(request, f'Test failed: {error}')
            return redirect('it_integrations')

        if section == 'site':
            from .notify import KEY_SITE_URL
            url = (request.POST.get('site_url') or '').strip().rstrip('/')
            if url and not url.startswith(('http://', 'https://')):
                messages.error(request,
                               'The site address must start with https:// or http://.')
            else:
                GlobalSetting.set(KEY_SITE_URL, url, request.user)
                messages.success(request, 'Site address saved.')
            return redirect('it_integrations')

        if section == 'ldap':
            GlobalSetting.set(ldap_auth.KEY_ENABLED, '1' if request.POST.get('enabled') else '0', request.user)
            GlobalSetting.set(ldap_auth.KEY_HOST, (request.POST.get('host') or '').strip(), request.user)
            GlobalSetting.set(ldap_auth.KEY_PORT, (request.POST.get('port') or '').strip(), request.user)
            GlobalSetting.set(ldap_auth.KEY_USE_SSL, '1' if request.POST.get('use_ssl') else '0', request.user)
            GlobalSetting.set(ldap_auth.KEY_BIND_DN, (request.POST.get('bind_dn') or '').strip(), request.user)
            bind_pw = (request.POST.get('bind_password') or '').strip()
            if bind_pw:  # keep the stored password when left blank
                GlobalSetting.set(ldap_auth.KEY_BIND_PASSWORD, bind_pw, request.user)
            GlobalSetting.set(ldap_auth.KEY_BASE_DN, (request.POST.get('base_dn') or '').strip(), request.user)
            GlobalSetting.set(ldap_auth.KEY_USER_FILTER, (request.POST.get('user_filter') or '').strip(), request.user)
            GlobalSetting.set(ldap_auth.KEY_DOMAIN, (request.POST.get('domain') or '').strip(), request.user)
            GlobalSetting.set(ldap_auth.KEY_AUTO_CREATE, '1' if request.POST.get('auto_create') else '0', request.user)
            GlobalSetting.set(ldap_auth.KEY_ATTR_FIRST, (request.POST.get('attr_first') or '').strip(), request.user)
            GlobalSetting.set(ldap_auth.KEY_ATTR_LAST, (request.POST.get('attr_last') or '').strip(), request.user)
            GlobalSetting.set(ldap_auth.KEY_ATTR_EMAIL, (request.POST.get('attr_email') or '').strip(), request.user)
            messages.success(request, 'LDAP / Active Directory settings saved.')
            return redirect('it_integrations')

        if section == 'ldap_test':
            ok, msg = ldap_auth.test_connection()
            if ok:
                messages.success(request, f'LDAP test: {msg}')
            else:
                messages.error(request, f'LDAP test failed: {msg}')
            return redirect('it_integrations')

        if section == 'ldap_sync':
            # The full import, from the page: one pass, right now. The
            # engine's own safety rules apply (core/ldap_sync.py) — this
            # button cannot deactivate local accounts or superusers.
            from . import ldap_sync
            report = ldap_sync.sync_users(
                dry_run=bool(request.POST.get('dry_run')),
                deactivate_missing=bool(request.POST.get('deactivate_missing')),
                ran_by=request.user.get_username())
            if report.get('ok'):
                messages.success(request, f'AD sync: {report["message"]}')
            else:
                messages.error(request, f'AD sync failed: {report["message"]}')
            return redirect('it_integrations')

        if section == 'ldap_test_user':
            test_user = (request.POST.get('test_username') or '').strip()
            test_pass = request.POST.get('test_password') or ''
            if not (test_user and test_pass):
                messages.error(request, 'Enter a username and password to test.')
            else:
                values, msg = ldap_auth.authenticate_ldap(test_user, test_pass)
                if values is not None:
                    shown = ', '.join(f'{k}={v}' for k, v in values.items() if v) or 'no attributes read'
                    messages.success(request, f'Credentials valid for "{test_user}" ({shown}).')
                else:
                    messages.error(request, f'User test failed: {msg}')
            return redirect('it_integrations')

        if section == 'whatsapp':
            GlobalSetting.set(whatsapp.KEY_ENABLED, '1' if request.POST.get('enabled') else '0', request.user)
            provider = request.POST.get('provider') or 'wasender'
            if provider in dict(whatsapp.PROVIDERS):
                GlobalSetting.set(whatsapp.KEY_PROVIDER, provider, request.user)
            GlobalSetting.set(whatsapp.KEY_COUNTRY_CODE, (request.POST.get('country_code') or '').strip(), request.user)
            GlobalSetting.set(whatsapp.KEY_WASENDER_BASE_URL, (request.POST.get('wasender_base_url') or '').strip(), request.user)
            api_key = (request.POST.get('wasender_api_key') or '').strip()
            if api_key:  # keep the stored key when left blank
                GlobalSetting.set(whatsapp.KEY_WASENDER_API_KEY, api_key, request.user)
            messages.success(request, 'WhatsApp messaging settings saved.')
            return redirect('it_integrations')

        if section == 'whatsapp_test':
            to = (request.POST.get('test_to') or '').strip()
            if not to:
                messages.error(request, 'Enter a phone number for the test message.')
            else:
                ok, error = whatsapp.send_whatsapp(
                    to, 'AJi Hub — WhatsApp integration test message. If you received this, the integration is working ✔',
                )
                if ok:
                    messages.success(request, f'Test message sent to {whatsapp.normalize_number(to)}. Check WhatsApp.')
                else:
                    messages.error(request, f'Test failed: {error}')
            return redirect('it_integrations')

        if section == 'assist_prompts':
            from .models import set_assist_prompts, DEFAULT_ASSIST_PROMPTS
            if 'reset' in request.POST:
                set_assist_prompts(DEFAULT_ASSIST_PROMPTS, request.user)
                messages.success(request, 'AJiAssist quick prompts reset to defaults.')
                return redirect('it_integrations')

            labels = request.POST.getlist('label')
            prompts = request.POST.getlist('prompt')
            delete_idx = request.POST.get('delete')
            rows = []
            for i, (label, prompt) in enumerate(zip(labels, prompts)):
                if delete_idx is not None and str(i) == delete_idx:
                    continue  # row deleted
                label, prompt = label.strip(), prompt.strip()
                if label and prompt:
                    rows.append({'label': label[:40], 'prompt': prompt[:300]})
            new_label = (request.POST.get('new_label') or '').strip()
            new_prompt = (request.POST.get('new_prompt') or '').strip()
            if new_label and new_prompt:
                rows.append({'label': new_label[:40], 'prompt': new_prompt[:300]})
            set_assist_prompts(rows, request.user)
            if delete_idx is not None:
                messages.success(request, 'Prompt deleted.')
            else:
                messages.success(request, f'AJiAssist quick prompts saved ({len(rows)} prompts).')
            return redirect('it_integrations')

    config = msgraph.get_config()
    ldap_config = ldap_auth.get_config()
    wa_config = whatsapp.get_config()
    from .models import get_assist_prompts
    return render(request, 'core/global_settings.html', {
        # Rendered inside the IT portal shell, which needs to know which tab is
        # lit. The page has no address of its own any more.
        'tab': 'integrations',
        'msgraph': config,
        'msgraph_configured': msgraph.is_configured(config),
        'msgraph_calendar_enabled': msgraph.calendar_enabled(),
        'has_secret': bool(config['client_secret']),
        'ldap': ldap_config,
        'ldap_configured': ldap_auth.is_configured(ldap_config),
        'ldap_has_password': bool(ldap_config['bind_password']),
        'ldap_default_filter': ldap_auth.DEFAULT_FILTER,
        'ldap_last_sync': ldap_sync_module.last_sync(),
        'wa': wa_config,
        'wa_configured': whatsapp.is_configured(wa_config),
        'wa_has_key': bool(wa_config['wasender_api_key']),
        'wa_providers': whatsapp.PROVIDERS,
        'wa_default_base': whatsapp.WASENDER_DEFAULT_BASE,
        'assist_prompt_rows': get_assist_prompts(),
        **_email_context(),
    })


def _email_context():
    """The SMTP route and the site address, for the Global Settings page."""
    from . import mailer
    from .notify import KEY_SITE_URL, site_url
    smtp = mailer.smtp_config()
    transport, note = mailer.transport_status()
    return {
        'smtp': smtp,
        'smtp_configured': mailer.smtp_is_configured(smtp),
        'smtp_has_password': bool(smtp['password']),
        'mail_transport': transport,
        'mail_note': note,
        'site_url': site_url(),
        'site_url_key': KEY_SITE_URL,
    }
