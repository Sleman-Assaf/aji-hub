# core/decorators.py
from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages


def role_required(*roles):
    """Restrict a view to users whose profile.role is one of `roles`.

    super_admin and Django superusers always pass. Note the guard must be
    driven purely by the *user's* role — an earlier version also tested
    whether 'super_admin' appeared in `roles`, which every caller passes,
    so the check silently allowed every authenticated user through.
    """
    allowed = set(roles) | {'super_admin'}

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect('login')
            # Superusers may have no UserProfile row at all.
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            try:
                user_role = request.user.profile.role
            except Exception:
                user_role = 'employee'
            if user_role not in allowed:
                messages.error(request, "You don't have permission to access this page.")
                return redirect('home')
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def hr_required(view_func):
    """Allow HR through — by profile role, the 'HR' group, or superuser.

    Deliberately shares `user_is_hr` with the `is_hr` template flag so the
    inline HR buttons and the views behind them can never disagree.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        from .models import user_is_hr
        if not user_is_hr(request.user):
            messages.error(request, "You don't have permission to do that.")
            return redirect('home')
        return view_func(request, *args, **kwargs)
    return wrapper


def content_editor_required(view_func):
    return role_required('content_editor', 'super_admin')(view_func)


def it_admin_required(view_func):
    return role_required('it_admin', 'super_admin')(view_func)
