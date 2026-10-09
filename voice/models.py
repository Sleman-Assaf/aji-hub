# voice/models.py
from django.db import models
from django.contrib.auth.models import User


class Suggestion(models.Model):
    """Anonymous suggestion box — no user FK by design."""
    text = models.TextField()
    submitted_at = models.DateTimeField(auto_now_add=True)
    is_reviewed = models.BooleanField(default=False)
    reviewer_note = models.TextField(blank=True)

    class Meta:
        ordering = ['-submitted_at']

    def __str__(self):
        return f"Anonymous suggestion ({self.submitted_at.date()})"


class Survey(models.Model):
    title = models.CharField(max_length=300)
    url = models.URLField()
    closes_on = models.DateField()
    added_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['closes_on']

    def __str__(self):
        return self.title
