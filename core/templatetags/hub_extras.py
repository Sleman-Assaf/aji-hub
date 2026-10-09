# core/templatetags/hub_extras.py
from django import template

from core.comments import may_delete as _may_delete
from core.comments import may_edit as _may_edit
from core.mentions import render_mentions

register = template.Library()


@register.filter(name='mentions', is_safe=True)
def mentions(value):
    """Render comment text with @mentions linked to profiles.

    Escapes the text itself, so templates must not add `|safe` on top.
    """
    return render_mentions(value)


@register.filter(name='initials')
def initials(user):
    """One or two letters for an avatar that has no photo.

    Templates were building this inline as first_name.0 + last_name.0, which
    gives an empty string for any account without a name on it. Every reply
    from such an account showed a filled avatar with nothing written in it.
    """
    if not user:
        return '?'
    name = (getattr(user, 'get_full_name', lambda: '')() or '').strip()
    if name:
        parts = name.split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[-1][0]).upper()
        return parts[0][:2].upper()
    username = (getattr(user, 'username', '') or '').strip()
    return username[:2].upper() if username else '?'


@register.filter(name='person')
def person(user):
    """Somebody's name as the directory spells it.

    Two records hold a name: the login account and the directory record, and for
    163 of the 1,263 accounts they disagree about capitals — the account says
    "AHMED DROUSHI" where the directory says "Ahmed Al Droushi". Both are the
    same person, but capitals read heavier than title case at the same weight, so
    a list drawing one name from each source looks like a list with two type
    styles in it. Every one of those 163 has a properly cased name in the
    directory, so the directory wins wherever there is one to ask.

    Falls back the way the rest of the Hub does: the account name, then the
    username, so a person with no directory record still has something to show.
    """
    if not user:
        return ''
    employee = getattr(user, 'employee', None)
    directory = (getattr(employee, 'full_name', '') or '').strip()
    if directory:
        return directory
    return (getattr(user, 'get_full_name', lambda: '')() or '').strip() \
        or getattr(user, 'username', '')


@register.filter(name='lookup')
def lookup(mapping, key):
    """One value out of a dict, by a key held in a variable.

    Django's dot syntax cannot do `dict[variable]`, which is what a table with a
    row per event and a column per channel needs on every cell. The alternative
    is flattening the whole matrix in the view into a shape the template can walk,
    which puts presentation logic where it does not belong.
    """
    if mapping is None:
        return ''
    try:
        return mapping.get(key, '')
    except AttributeError:
        return ''


#: Characters that end a CSS string early, or close the url() around it.
#: Percent-encoded rather than backslash-escaped because the result also has to
#: survive being an HTML attribute value, and a backslash there is its own
#: argument between the HTML parser and the CSS parser.
_CSS_UNSAFE = {
    "'": '%27',
    '"': '%22',
    '(': '%28',
    ')': '%29',
    '\\': '%5C',
    ' ': '%20',
    '\n': '%0A',
    '\r': '%0D',
    '\t': '%09',
}


@register.filter(name='css_url')
def css_url(value):
    """A media URL safe to drop inside `background-image:url('...')`.

    Django leaves an apostrophe alone when it builds a file URL — it is a legal
    URL character — so a photo filed under `independence_day_'25` produces

        url('/media/brand/booth/independence_day_'25/photos/9.jpg')

    where the apostrophe in the path closes the CSS string four segments early.
    The declaration is then invalid and the browser drops it, which is why that
    album's tile rendered as a blank white card while the file itself served
    perfectly well over HTTP.

    Percent-encoding is a plain URL escape, so the server resolves it to exactly
    the same file.
    """
    if not value:
        return ''
    return ''.join(_CSS_UNSAFE.get(ch, ch) for ch in str(value))


@register.filter(name='may_edit')
def may_edit(obj, user):
    """`{% if comment|may_edit:request.user %}` — the author, and only them."""
    return _may_edit(obj, user)


@register.filter(name='may_delete')
def may_delete(obj, user):
    """The author, or somebody whose job is moderation."""
    return _may_delete(obj, user)
