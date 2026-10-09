# core/people.py
"""One people search, for every picker on the Hub.

There were three of these: the @mention autocomplete, the Exchange member
picker and the chat's new-conversation list, each with its own idea of what a
result looks like and which fields to match. They drifted — one searched the
employee record's full name, another only the auth first/last name, so the same
query found different people depending on which box you typed it into.
"""
from django.contrib.auth.models import User
from django.db.models import Q

#: Enough to fill a dropdown without shipping the staff list.
LIMIT = 20


def search_people(q='', exclude=(), limit=LIMIT, restrict_to=None):
    """[{id, name, role, photo}] for the people matching `q`.

    `exclude` takes users or ids — normally the person searching, plus anyone
    already chosen. `restrict_to` narrows the pool to a queryset, which is how
    a private community's picker offers only its own members.
    """
    users = restrict_to if restrict_to is not None else User.objects.all()
    users = users.filter(is_active=True).select_related('employee')

    q = (q or '').strip()
    if q:
        users = users.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q)
                             | Q(username__icontains=q)
                             | Q(employee__full_name__icontains=q))

    ids = [getattr(u, 'pk', u) for u in exclude]
    if ids:
        users = users.exclude(pk__in=ids)

    out = []
    for u in users.order_by('first_name', 'last_name').distinct()[:limit]:
        out.append(person_json(u))
    return out


def person_json(user):
    """The shape every picker draws: a name, what they do, and a face."""
    emp = getattr(user, 'employee', None)
    return {
        'id': user.pk,
        'name': (getattr(emp, 'full_name', '') or user.get_full_name()
                 or user.username),
        'role': getattr(emp, 'role', '') or '',
        'photo': emp.photo.url if (emp is not None and emp.photo) else '',
        'username': user.username,
    }
