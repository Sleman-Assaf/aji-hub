# messaging/urls.py
"""
Fixed routes come first: `<int:pk>/` would otherwise swallow anything that looks
like a number, and `msg/` and `groups/` are not conversation ids.
"""
from django.urls import path

from . import call_views, stress_views, views

urlpatterns = [
    # ── API: calls ──
    # Before `<int:pk>/`, like everything else fixed: `call/` is not a
    # conversation id. Joining is addressed by conversation ("call these
    # people"); everything after it is addressed by call, because by then there
    # is one to name.
    # The log is addressed by nothing at all — it is every call in every
    # conversation you are in — so it sits above the ones addressed by call id.
    path('calls/log/', call_views.api_call_log, name='api_call_log'),
    # ── the capacity monitor and stress test (Intranet Admin / IT only) ──
    path('calls/stress/',          stress_views.stress_page,
         name='call_stress'),
    path('calls/stress/status/',   stress_views.api_stress_status,
         name='api_call_stress_status'),
    path('calls/stress/run/',      stress_views.api_stress_run,
         name='api_call_stress_run'),
    path('calls/stress/cleanup/',  stress_views.api_stress_cleanup,
         name='api_call_stress_cleanup'),
    path('call/<int:pk>/decline/', call_views.api_call_decline,
         name='api_call_decline'),
    path('call/<int:pk>/leave/',   call_views.api_call_leave,  name='api_call_leave'),
    path('call/<int:pk>/state/',   call_views.api_call_state,  name='api_call_state'),
    path('call/<int:pk>/signal/',  call_views.api_call_signal, name='api_call_signal'),
    path('call/<int:pk>/bridge/offer/', call_views.api_call_bridge_offer,
         name='api_call_bridge_offer'),
    path('call/<int:pk>/sync/',    call_views.api_call_sync,   name='api_call_sync'),
    path('call/<int:pk>/invite/',  call_views.api_call_invite,
         name='api_call_invite'),
    path('call/<int:pk>/invitees/', call_views.api_call_invitees,
         name='api_call_invitees'),

    # ── API: the list and the badge ──
    path('api/conversations/',  views.api_conversations, name='api_conversations'),
    path('api/unread/',         views.api_unread,        name='api_unread'),
    path('api/poll/',           views.api_poll,          name='api_poll'),
    path('api/people/',         views.api_people,        name='api_chat_people'),

    # ── API: one message ──
    path('msg/<int:pk>/edit/',  views.api_message_edit,  name='api_message_edit'),
    path('msg/<int:pk>/hide/',  views.api_message_hide,  name='api_message_hide'),
    path('msg/<int:pk>/react/', views.api_react,         name='api_react'),

    # ── API: groups ──
    path('groups/new/',         views.api_group_create,  name='api_group_create'),

    # ── Pages ──
    path('',                        views.messaging_home, name='messaging'),
    path('with/<int:user_pk>/',     views.get_or_create_conversation,
         name='start_conversation'),
    path('with/<int:user_pk>/api/', views.api_start_with, name='api_start_with'),

    # ── API: one conversation ──
    path('<int:pk>/',               views.conversation_detail,
         name='conversation_detail'),
    path('<int:pk>/api/messages/',  views.api_messages,     name='api_messages'),
    path('<int:pk>/send/',          views.api_send,         name='api_send'),
    path('<int:pk>/pin/',           views.api_pin,          name='api_pin'),
    path('<int:pk>/delete/',        views.api_delete_chat,  name='api_delete_chat'),
    path('<int:pk>/read/',          views.api_mark_read,    name='api_mark_read'),
    path('<int:pk>/group/',         views.api_group_update, name='api_group_update'),
    # Start a call here, or answer the one already going on — the same request
    # either way. See call_views.api_call_join.
    path('<int:pk>/call/join/',     call_views.api_call_join, name='api_call_join'),
]
