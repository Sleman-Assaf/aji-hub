# core/context_processors.py
import os
from functools import wraps

from django.conf import settings

from .models import Notification

_ASSET_V = None


def once_per_request(processor):
    """Run a context processor once a request, not once a template render.

    A context processor is meant to run once. The CMS renders every block with
    `render_to_string(..., request=request)`, and passing the request builds a
    fresh RequestContext each time — which re-runs all of them. The homepage has
    twenty-four blocks, so it rebuilt the navigation twenty-four times and asked
    for the same page record twenty-four times: 305 queries and 1.8 seconds for
    a page that needs about a dozen.

    Caching on the request rather than in a module global because these answers
    depend on who is asking. The attribute name carries the module so two
    processors called `context` in different apps cannot collide.
    """
    key = '_ctx_once_%s_%s' % (processor.__module__.replace('.', '_'), processor.__name__)

    @wraps(processor)
    def wrapper(request):
        cached = getattr(request, key, None)
        if cached is None:
            cached = processor(request)
            try:
                setattr(request, key, cached)
            except AttributeError:
                pass          # not all request-likes accept attributes
        return cached

    return wrapper


def asset_version():
    """Cache-busting stamp for the stylesheet and the scripts.

    Django's HashedFilesMixin skips content hashing while DEBUG is on, so
    {% static %} returns an unchanging URL in development and browsers keep
    serving a stale file long after it was edited. Stamping the newest mtime onto
    the URL makes every edit land. Computed per request in DEBUG (the files
    change constantly) and cached once otherwise.

    The scripts are in here too. When the chat engine moved out of the templates
    and into static/js, a stamp that only watched the stylesheet would have left
    browsers running yesterday's chat with today's CSS.
    """
    global _ASSET_V
    if _ASSET_V is not None and not settings.DEBUG:
        return _ASSET_V

    newest = 0
    css = os.path.join(settings.BASE_DIR, 'static', 'css', 'aji_hub.css')
    watched = [css]
    js_dir = os.path.join(settings.BASE_DIR, 'static', 'js')
    try:
        watched += [os.path.join(js_dir, n) for n in os.listdir(js_dir)
                    if n.endswith('.js')]
    except OSError:
        pass
    for path in watched:
        try:
            newest = max(newest, int(os.path.getmtime(path)))
        except OSError:
            continue
    _ASSET_V = str(newest or 0)
    return _ASSET_V


@once_per_request
def hub_context(request):
    ctx = {'asset_version': asset_version()}
    if request.user.is_authenticated:
        # The chat popup is on every page, and both it and the full page read
        # their limits from the same place rather than each hardcoding a number
        # that then disagrees with what the server actually accepts.
        from messaging.models import MAX_ATTACHMENT_BYTES, QUICK_REACTIONS
        from .models import NotificationChannel, NotificationEvent, NotificationPreference
        ctx['chat_max_bytes'] = MAX_ATTACHMENT_BYTES
        ctx['chat_quick'] = ','.join(QUICK_REACTIONS)
        # Whether a new message may raise a desktop pop-up. Read here rather than
        # in the engine because the answer is a preference, not a browser fact.
        try:
            ctx['chat_popup'] = NotificationPreference.for_user(request.user).wants(
                NotificationEvent.CHAT, NotificationChannel.BROWSER)
        except Exception:
            ctx['chat_popup'] = True
        from .models import (is_intranet_admin, get_assist_prompts, user_is_hr,
                             user_can_manage_settings)
        ctx['is_intranet_admin'] = is_intranet_admin(request.user)
        # Whether the Global settings entry appears in the user menu. The same
        # rule the view uses, so the menu cannot offer a page that then refuses.
        ctx['can_manage_settings'] = user_can_manage_settings(request.user)
        # Drives the inline HR add/edit/delete controls on news and calendar.
        ctx['is_hr'] = user_is_hr(request.user)
        ctx['assist_prompts'] = get_assist_prompts()
        ctx['notif_unread_count'] = Notification.objects.filter(
            recipient=request.user, is_read=False
        ).count()
        # attach employee profile if exists
        try:
            ctx['current_employee'] = request.user.employee
        except Exception:
            ctx['current_employee'] = None
        # attach hub role
        try:
            ctx['user_role'] = request.user.profile.role
        except Exception:
            ctx['user_role'] = 'employee'
    # AJiAssist session for the panel in base.html
    if request.user.is_authenticated:
        try:
            from assist.models import AssistSession
            active = AssistSession.objects.filter(
                user=request.user, is_active=True
            ).prefetch_related('messages').first()
            if not active:
                active = AssistSession.objects.create(user=request.user, is_active=True)
            ctx['assist_session']  = active
            ctx['assist_messages'] = active.messages.filter(answer__gt='').order_by('created_at')
            ctx['assist_past']     = [
                s for s in AssistSession.objects.filter(
                    user=request.user, is_active=False
                ).prefetch_related('messages').order_by('-started_at')[:20]
                if s.messages.exists()
            ]
        except Exception:
            ctx['assist_session']  = None
            ctx['assist_messages'] = []
            ctx['assist_past']     = []

    return ctx
