# core/middleware.py
"""
Tracks employee presence ("Active now" status).
Updates Employee.last_seen at most once every 60 seconds per user
to avoid hammering the DB on every request.
"""
from django.utils import timezone
from datetime import timedelta


class UpdateLastSeenMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        user = getattr(request, 'user', None)
        if user and user.is_authenticated:
            try:
                emp = user.employee
            except Exception:
                emp = None

            if emp:
                now = timezone.now()
                # Throttle: only write to DB if >60s since last update
                if not emp.last_seen or (now - emp.last_seen) > timedelta(seconds=60):
                    emp.last_seen = now
                    emp.save(update_fields=['last_seen'])

        return response
