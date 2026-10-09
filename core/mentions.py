# core/mentions.py
"""@mention support shared by every commenting surface in the Hub.

Comments exist in more than one app (news article comments, exchange thread
replies), so parsing, the notification fan-out and rendering all live here
instead of being reimplemented per app.
"""
import re

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils.html import escape
from django.utils.safestring import mark_safe

from .models import Notification, NotificationPreference

# Usernames in this system look like "ayah.haji.198", so dots and digits are
# part of the token. A trailing dot is sentence punctuation, not the username,
# which is why the token may not end in '.' or '-'.
MENTION_RE = re.compile(r'@([A-Za-z0-9_](?:[A-Za-z0-9_.\-]*[A-Za-z0-9_])?)')


def extract_mentioned_users(text, exclude=None):
    """Resolve the @tokens in `text` to active Users, in order of appearance."""
    if not text:
        return []
    tokens = list(dict.fromkeys(MENTION_RE.findall(text)))
    if not tokens:
        return []

    found, seen = [], set()
    for token in tokens:
        user = User.objects.filter(is_active=True, username__iexact=token).first()
        if user is None:
            # Typed by hand rather than picked from the autocomplete; only
            # accept a prefix when it is unambiguous.
            matches = list(
                User.objects.filter(is_active=True, username__istartswith=token)[:2]
            )
            user = matches[0] if len(matches) == 1 else None
        if user is None or user.pk in seen:
            continue
        if exclude is not None and user.pk == getattr(exclude, 'pk', None):
            continue
        seen.add(user.pk)
        found.append(user)
    return found


def notify_mentions(text, author, title, link, body='', case=None,
                    values=None):
    """Tell everyone @mentioned in `text` that they were named.

    Goes through core.notify, so it respects each person's channels — the bell,
    a desktop pop-up, an email, or none of them — instead of deciding on their
    behalf that a bell row is the only possible answer.

    `case` is passed straight through. It is the caller's to name rather than
    this function's, because four surfaces share this helper and a mention in a
    news comment is not the same email as a mention in a chat message: they read
    differently, link differently, and an administrator should be able to switch
    one off without losing the others.

    Returns the users actually reached, which is what the caller records as the
    reply's mentions.
    """
    from .notify import Event, notify

    notified = []
    for user in extract_mentioned_users(text, exclude=author):
        out = notify(user, Event.MENTION, title,
                     body=(body or text)[:500], link=link, actor=author,
                     email_subject=title, case=case,
                     values={**(values or {}),
                             'name': user.get_full_name() or user.username})
        # Named in the comment either way: the record of who was mentioned is
        # about the text, not about whether their settings let a bell ring.
        notified.append(user)
    return notified


def render_mentions(text):
    """Escape `text`, then turn each resolvable @mention into a profile link."""
    if not text:
        return ''

    by_username = {u.username.lower(): u for u in extract_mentioned_users(text)}

    def replace(match):
        token = match.group(1).lower()
        user = by_username.get(token)
        if user is None:
            # Mirror the unambiguous-prefix fallback used when parsing.
            for username, candidate in by_username.items():
                if username.startswith(token):
                    user = candidate
                    break
        if user is None:
            return match.group(0)

        label = escape(user.get_full_name() or user.username)
        employee = getattr(user, 'employee', None)
        if employee is not None:
            url = reverse('profile_detail', args=[employee.pk])
            return f'<a class="mention" href="{url}">@{label}</a>'
        return f'<span class="mention">@{label}</span>'

    # Escaping first is safe: usernames contain no HTML-significant characters,
    # so the pattern still matches and the surrounding text stays inert.
    return mark_safe(MENTION_RE.sub(replace, escape(text)))
