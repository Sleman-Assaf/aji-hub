# assist/models.py
from django.db import models
from django.contrib.auth.models import User


class AssistSession(models.Model):
    """A named conversation session with AJiAssist."""
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='assist_sessions')
    name       = models.CharField(max_length=200, blank=True, help_text='Auto-generated from first question')
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at   = models.DateTimeField(null=True, blank=True)
    is_active  = models.BooleanField(default=True, help_text='Current login session')

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"{self.user.get_full_name()} — {self.name or 'Unnamed'} ({self.started_at:%d %b %Y})"

    def auto_name(self):
        """Generate a name from the first message."""
        first = self.messages.order_by('created_at').first()
        if first:
            q = first.question.strip()
            # Truncate to ~60 chars at word boundary
            if len(q) > 60:
                q = q[:57].rsplit(' ', 1)[0] + '…'
            return q
        return f'Session {self.started_at:%d %b %Y %H:%M}'

    @property
    def message_count(self):
        return self.messages.count()


class AssistMessage(models.Model):
    user     = models.ForeignKey(User, on_delete=models.CASCADE, related_name='assist_messages')
    session  = models.ForeignKey(AssistSession, on_delete=models.CASCADE,
                                  related_name='messages', null=True, blank=True)
    question = models.TextField()
    answer   = models.TextField(blank=True)
    context_snapshot = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.user.get_full_name()}: {self.question[:80]}"
