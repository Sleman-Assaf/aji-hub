from django.contrib import admin

from .models import Attachment, Community, JoinRequest, Thread, ThreadReply


@admin.register(Community)
class CommunityAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'visibility', 'created_by', 'deleted_at')
    list_filter = ('visibility',)
    prepopulated_fields = {'slug': ('name',)}
    filter_horizontal = ('members',)

    def get_queryset(self, request):
        # The admin is where a removed community is inspected, so it must not
        # use the manager that hides them.
        return Community.all_objects.all()


@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    list_display = ('title', 'community', 'author', 'thread_type', 'pinned', 'created_at')
    list_filter = ('community', 'thread_type', 'pinned')


@admin.register(JoinRequest)
class JoinRequestAdmin(admin.ModelAdmin):
    list_display = ('user', 'community', 'status', 'created_at')
    list_filter = ('status',)


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ('name', 'community', 'thread', 'attach_type', 'pinned', 'created_at')
    list_filter = ('attach_type', 'pinned')


admin.site.register(ThreadReply)
