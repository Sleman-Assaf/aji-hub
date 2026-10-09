"""Make the Django test client usable on this interpreter.

Django 5.0.6 copies a template context with `copy(super())`
(django/template/context.py). Up to Python 3.13 that quietly produced a copy of
the underlying instance; on Python 3.14 it copies the `super` proxy itself, which
has no `__dict__`, and the next line raises

    AttributeError: 'super' object has no attribute 'dicts'

The test client copies the context of every template it renders, so *any* test
that fetches a page through `self.client` dies here — and because the failure
happens inside Django's own 500 handler while it renders the debug page, what
surfaces is this AttributeError instead of whatever the test was actually
asserting. It looks like a bug in the test.

Django fixed this after 5.0. Until this project moves off 5.0.6 or off Python
3.14, this replaces the two-line method with one that does the same thing without
copying a proxy object. Imported from `core/tests/__init__.py` and
`portal/tests/__init__.py`, so it is in place before any test module loads.
Deliberately not applied in production: nothing in the request path copies a
context, and silently patching a template internal for every request is a bigger
risk than the bug.
"""
from django.template.context import BaseContext


def _copy(self):
    duplicate = self.__class__.__new__(self.__class__)
    duplicate.__dict__.update(self.__dict__)
    # The one thing the original was trying to do: the list of scopes is copied
    # so pushing onto the duplicate cannot alter the original, while the
    # dictionaries inside it stay shared, exactly as a shallow copy should.
    duplicate.dicts = self.dicts[:]
    return duplicate


def apply():
    """Patch it, once, and only if this interpreter needs it."""
    if getattr(BaseContext, '_aji_copy_shim', False):
        return
    try:
        BaseContext().__copy__()
    except AttributeError:
        BaseContext.__copy__ = _copy
        BaseContext._aji_copy_shim = True
