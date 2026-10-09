# cms/admin.py
from django.contrib import admin
from django.utils.html import format_html

from .models import Page, Section, SectionItem


class SectionItemInline(admin.TabularInline):
    model = SectionItem
    extra = 1
    fields = ('order', 'is_visible', 'title', 'meta', 'badge', 'icon',
              'image', 'file', 'url', 'button_label')
    ordering = ('order',)


class SectionInline(admin.TabularInline):
    model = Section
    extra = 0
    fields = ('order', 'type', 'title', 'is_visible', 'pinned', 'audience')
    ordering = ('-pinned', 'order')
    show_change_link = True


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ('title', 'slug', 'is_published', 'state', 'audience',
                    'show_in_nav', 'updated_at')
    list_filter = ('is_published', 'audience', 'show_in_nav')
    search_fields = ('title', 'slug', 'subtitle')
    prepopulated_fields = {'slug': ('title',)}
    filter_horizontal = ('audience_groups',)
    inlines = [SectionInline]
    fieldsets = (
        (None, {'fields': ('title', 'slug', 'eyebrow', 'subtitle')}),
        ('Visibility', {'fields': ('is_published', 'audience', 'audience_groups',
                                   'show_in_nav', 'nav_order')}),
        ('Scheduling', {'fields': ('publish_at', 'archive_at'),
                        'description': 'Evaluated when the page is read — no '
                                       'background job is involved.'}),
    )

    @admin.display(description='State')
    def state(self, obj):
        colours = {'live': '#1F9D55', 'scheduled': '#A87A00', 'archived': '#C2410C'}
        s = obj.schedule_state
        return format_html('<b style="color:{}">{}</b>', colours.get(s, '#76756A'), s)

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'page', 'type', 'order', 'is_visible', 'pinned', 'audience')
    list_filter = ('type', 'is_visible', 'pinned', 'audience', 'page')
    search_fields = ('title', 'subtitle', 'body')
    filter_horizontal = ('audience_groups',)
    inlines = [SectionItemInline]
    fieldsets = (
        (None, {'fields': ('page', 'type', 'title', 'subtitle', 'body', 'icon', 'image')}),
        ('Placement', {'fields': ('order', 'pinned', 'is_visible')}),
        ('Visibility', {'fields': ('audience', 'audience_groups')}),
        ('Scheduling', {'fields': ('publish_at', 'archive_at')}),
        ('Advanced', {'fields': ('config',), 'classes': ('collapse',),
                      'description': 'Per-type settings such as columns, limit '
                                     'or an embed URL.'}),
    )
