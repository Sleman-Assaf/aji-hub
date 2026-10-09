# core/comments.py
"""Editing and deleting your own comment, shared by every surface that has one.

There are three: news article comments, Exchange thread replies, and the
recognition thread under a praise or a certification. All three had the same
gap — once posted, a comment was permanent, and a typo or a wrong name could
only be removed by asking IT to open a database shell.

The rules and the plumbing live here rather than three times over, because the
three surfaces answered "who may do this" differently the moment anybody wrote
them separately. They do not differ. The author owns their words; HR and the
Intranet Admin can take a comment down because moderation is their job; nobody
else touches either.

Note the asymmetry, which is deliberate. Editing is the author's alone: nobody
should be able to change what somebody else is recorded as having said, and a
moderator who could would be able to put words in a colleague's mouth. Deleting
is the author's *and* a moderator's, because the reason to remove a comment is
usually that its author will not.
"""
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from .models import is_intranet_admin, user_is_hr


def is_author(obj, user):
    return bool(user and user.is_authenticated and obj.author_id == user.pk)


def is_moderator(user):
    return bool(user and user.is_authenticated
                and (user_is_hr(user) or is_intranet_admin(user)))


def may_edit(obj, user):
    """Only the author. See the note in the module docstring."""
    return is_author(obj, user)


def may_delete(obj, user):
    """The author, or somebody whose job is moderation."""
    return is_author(obj, user) or is_moderator(user)


def apply_edit(request, obj, field):
    """Save an edited comment, or raise.

    Returns True when the text changed, False when it was submitted unchanged or
    empty — an empty edit is not a delete, because they are different buttons and
    conflating them turns a mistyped save into a lost comment.
    """
    if not may_edit(obj, request.user):
        raise PermissionDenied
    text = (request.POST.get(field) or '').strip()
    if not text or text == getattr(obj, field):
        return False
    setattr(obj, field, text)
    obj.edited_at = timezone.now()
    fields = [field, 'edited_at']
    obj.save(update_fields=fields)
    return True


def apply_delete(request, obj):
    """Delete a comment, or raise. Hard delete: it is the author's own text."""
    if not may_delete(obj, request.user):
        raise PermissionDenied
    obj.delete()
