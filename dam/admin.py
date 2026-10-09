# dam/admin.py
from django.contrib import admin

from .models import Asset, AssetVersion, Folder, Space, Tag


@admin.register(Space)
class SpaceAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'audience', 'order')
    prepopulated_fields = {'slug': ('name',)}
    filter_horizontal = ('audience_groups',)


@admin.register(Folder)
class FolderAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'space', 'pinned', 'colour', 'audience',
                    'owner', 'review_date')
    list_filter = ('space', 'pinned', 'audience', 'colour')
    search_fields = ('name', 'description')
    filter_horizontal = ('audience_groups',)
    fieldsets = (
        (None, {'fields': ('space', 'parent', 'name', 'slug', 'description')}),
        ('Presentation', {'fields': ('icon', 'cover', 'colour', 'pinned', 'order')}),
        ('Governance', {'fields': ('owner', 'review_date')}),
        ('Permissions', {'fields': ('audience', 'audience_groups')}),
    )


class AssetVersionInline(admin.TabularInline):
    model = AssetVersion
    extra = 0
    fields = ('version', 'file', 'note', 'pretty_size', 'replaced_by', 'created_at')
    readonly_fields = ('version', 'file', 'note', 'pretty_size', 'replaced_by', 'created_at')
    can_delete = False

    def has_add_permission(self, request, obj):
        return False


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ('name', 'folder', 'format_label', 'version',
                    'pretty_size', 'archived', 'updated_at')
    list_filter = ('archived', 'folder__space', 'tags')
    search_fields = ('name', 'description', 'file')
    filter_horizontal = ('tags',)
    readonly_fields = ('size', 'version', 'thumbnail')
    inlines = [AssetVersionInline]


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}
