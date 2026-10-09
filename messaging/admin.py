from django.contrib import admin

from .models import (Call, CallPeer, CallSignal, ChatState, Conversation,
                     Message, MessageFile, Reaction)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'kind', 'name', 'created_by', 'updated_at')
    list_filter = ('kind',)
    filter_horizontal = ('participants',)


class MessageFileInline(admin.TabularInline):
    model = MessageFile
    extra = 0


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('sender', 'conversation', 'short', 'system', 'edited_at', 'sent_at')
    list_filter = ('system',)
    search_fields = ('text',)
    inlines = [MessageFileInline]

    @admin.display(description='Message')
    def short(self, obj):
        return obj.text[:60] or (f'[{obj.filename}]' if obj.filename else '—')


@admin.register(ChatState)
class ChatStateAdmin(admin.ModelAdmin):
    """Per-person state. Here mostly so "why is this chat missing for one person
    but not the other?" has an answer that does not need a shell."""
    list_display = ('user', 'conversation', 'pinned', 'hidden', 'cleared_at',
                    'last_read_at')
    list_filter = ('pinned', 'hidden')


@admin.register(Reaction)
class ReactionAdmin(admin.ModelAdmin):
    list_display = ('emoji', 'user', 'message', 'created_at')


class CallPeerInline(admin.TabularInline):
    model = CallPeer
    extra = 0
    readonly_fields = ('peer_id', 'joined_at', 'last_ping')


@admin.register(Call)
class CallAdmin(admin.ModelAdmin):
    """Here so that "the call would not connect" has somewhere to start: who was
    in it, when they joined, and when the server stopped hearing from them."""
    list_display = ('__str__', 'started_by', 'status', 'started_at',
                    'ended_at', 'ended_reason')
    list_filter = ('status', 'ended_reason')
    filter_horizontal = ('declined_by',)
    inlines = [CallPeerInline]


@admin.register(CallSignal)
class CallSignalAdmin(admin.ModelAdmin):
    """Only ever populated during a live call — a call that has ended takes its
    signalling with it. Rows sitting here for an ended call mean the clean-up in
    Call.end did not run."""
    list_display = ('call', 'kind', 'from_peer', 'to_peer', 'created_at')
    list_filter = ('kind',)
