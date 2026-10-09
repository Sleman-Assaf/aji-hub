from django.contrib import admin
from .models import Suggestion, Survey

@admin.register(Suggestion)
class SuggestionAdmin(admin.ModelAdmin):
    list_display = ('submitted_at', 'is_reviewed')
    list_filter = ('is_reviewed',)

@admin.register(Survey)
class SurveyAdmin(admin.ModelAdmin):
    list_display = ('title', 'closes_on', 'active')
