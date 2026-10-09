from django.contrib import admin
from .models import UserProfile, Notification, NotificationPreference

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    """Assign the HR role here to let someone manage news and calendar
    events from the front end."""
    list_display = ('user', 'role')
    list_filter = ('role',)
    search_fields = ('user__username', 'user__first_name', 'user__last_name')

@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    """People manage these themselves at /notifications/settings/. This is here
    for answering "why did they not get the email?" without a shell."""
    list_display = ('user', 'chat_email', 'mention_email', 'approval_email',
                    'renewal_email',
                    'digest_email', 'digest_hour', 'digest_last_sent')
    list_filter = ('digest_email', 'chat_browser', 'mention_email', 'approval_email')
    search_fields = ('user__username', 'user__first_name', 'user__last_name')

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'type', 'title', 'is_read', 'created_at')
    list_filter = ('type', 'is_read')


from .models import GlobalSetting


@admin.register(GlobalSetting)
class GlobalSettingAdmin(admin.ModelAdmin):
    list_display = ('key', 'updated_by', 'updated_at')
    search_fields = ('key',)
