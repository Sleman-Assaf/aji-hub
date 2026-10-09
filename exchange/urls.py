# exchange/urls.py
"""
Ordering matters here. `<slug:slug>/<int:pk>/` would swallow anything that
looked like a number, and `<slug:slug>/` would swallow "new" — which is why the
fixed routes come first and `Community.unique_slug` refuses to mint a slug that
matches one of them.
"""
from django.urls import path

from . import views


def _redirect_thread(request, slug, pk):
    """Redirect old /exchange/<slug>/thread/<pk>/ → /exchange/<slug>/<pk>/"""
    from django.shortcuts import redirect
    return redirect('thread_detail', slug=slug, pk=pk)


urlpatterns = [
    # ── fixed routes, before anything that captures a slug ──
    path('',          views.exchange_home,    name='exchange_home'),
    path('new/',      views.create_community, name='create_community'),
    path('deleted/',  views.community_bin,    name='community_bin'),
    path('api/people/', views.people_search,  name='exchange_people_search'),
    path('reply/<int:pk>/edit/',   views.reply_edit,   name='reply_edit'),
    path('reply/<int:pk>/delete/', views.reply_delete, name='reply_delete'),

    # ── one community ──
    path('<slug:slug>/',          views.community_detail, name='community_detail'),
    path('<slug:slug>/new/',      views.create_thread,    name='create_thread'),
    path('<slug:slug>/update/',   views.community_update, name='community_update'),
    path('<slug:slug>/delete/',   views.community_delete, name='community_delete'),
    path('<slug:slug>/restore/',  views.community_restore, name='community_restore'),
    path('<slug:slug>/purge/',    views.community_purge,   name='community_purge'),
    path('<slug:slug>/ask/',      views.community_join_request, name='community_join_request'),
    path('<slug:slug>/members/',  views.community_members_edit, name='community_members_edit'),
    path('<slug:slug>/members/search/', views.community_member_search,
         name='community_member_search'),
    path('<slug:slug>/requests/<int:pk>/<str:action>/', views.join_request_action,
         name='join_request_action'),

    # ── documents ──
    path('<slug:slug>/docs/add/',            views.attachment_add,    name='attachment_add'),
    path('<slug:slug>/docs/<int:pk>/star/',  views.attachment_pin,    name='attachment_pin'),
    path('<slug:slug>/docs/<int:pk>/remove/', views.attachment_delete, name='attachment_delete'),

    # ── threads ──
    # Legacy URL redirect (old bookmarks), kept above the numeric route.
    path('<slug:slug>/thread/<int:pk>/', _redirect_thread),
    path('<slug:slug>/<int:pk>/',        views.thread_detail, name='thread_detail'),
    path('<slug:slug>/<int:pk>/edit/',   views.thread_edit,   name='thread_edit'),
    path('<slug:slug>/<int:pk>/star/',   views.thread_pin,    name='thread_pin'),
    path('<slug:slug>/<int:pk>/remove/', views.thread_delete, name='thread_delete'),
    path('<slug:slug>/<int:pk>/share/',  views.thread_share,  name='thread_share'),
    path('<slug:slug>/<int:pk>/share/who/', views.thread_share_targets,
         name='thread_share_targets'),
]
