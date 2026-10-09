from django.contrib import admin
from .models import (NewsCategory, NewsArticle, NewsAttachment, NewsComment,
                    CalendarEvent, HRAnnouncement, HRAttachment, HRCategory,
                    Project, WhatsAppChannel)


@admin.register(HRCategory)
class HRCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}


class HRAttachmentInline(admin.TabularInline):
    model = HRAttachment
    extra = 1
    fields = ('file', 'caption')


@admin.register(HRAnnouncement)
class HRAnnouncementAdmin(admin.ModelAdmin):
    """HR publishes and schedules updates here or from the Hub front end."""
    list_display = ('title', 'category', 'date', 'status', 'pinned', 'views')
    list_filter = ('status', 'pinned', 'category')
    search_fields = ('title', 'body', 'excerpt')
    prepopulated_fields = {'slug': ('title',)}
    inlines = [HRAttachmentInline]
    fieldsets = (
        (None, {'fields': ('title', 'slug', 'category', 'tag', 'author_name', 'date')}),
        ('Content', {'fields': ('image', 'excerpt', 'body')}),
        ('Publishing', {'fields': ('status', 'pinned', 'publish_at', 'archive_at'),
                        'description': 'Scheduling is applied when the page is '
                                       'read — no background job needed.'}),
    )


@admin.register(WhatsAppChannel)
class WhatsAppChannelAdmin(admin.ModelAdmin):
    """Upload a new QR image here and the Connect modal updates itself."""
    list_display = ('title', 'is_active', 'order', 'has_qr')
    list_editable = ('is_active', 'order')
    fields = ('title', 'description', 'qr_image', 'join_url', 'order', 'is_active')

    @admin.display(boolean=True, description='QR uploaded')
    def has_qr(self, obj):
        return bool(obj.qr_image)

@admin.register(NewsCategory)
class NewsCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}


class NewsAttachmentInline(admin.TabularInline):
    model = NewsAttachment
    extra = 1
    fields = ('file', 'caption', 'uploaded_by')

@admin.register(NewsArticle)
class NewsArticleAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'date', 'published')
    list_filter = ('published', 'category')
    search_fields = ('title',)
    prepopulated_fields = {'slug': ('title',)}
    inlines = [NewsAttachmentInline]

@admin.register(NewsComment)
class NewsCommentAdmin(admin.ModelAdmin):
    list_display = ('article', 'author', 'created_at')
    search_fields = ('text',)
    filter_horizontal = ('mentions',)

@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    list_display = ('title', 'date', 'end_date', 'event_type', 'is_public', 'created_by')
    list_filter = ('event_type', 'is_public', 'all_day')
    search_fields = ('title', 'location', 'description')

# HRAnnouncement is registered above with the fuller publishing admin.

@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ('title', 'location', 'division', 'status', 'year')
    list_filter = ('status', 'division', 'year')
