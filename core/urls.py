# core/urls.py
from django.urls import path
from django.views.generic import RedirectView
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('search/', views.search, name='search'),
    path('notifications/', views.notifications_api, name='notifications_api'),
    path('notifications/read/', views.notifications_mark_read, name='notifications_mark_read'),
    path('notifications/<int:pk>/read/', views.notification_mark_one_read, name='notification_mark_one_read'),
    path('notifications/history/', views.alerts_history, name='alerts_history'),
    path('notifications/settings/', views.notification_settings, name='notification_settings'),
    # Global Settings moved into the IT portal, where the rest of the
    # administration lives. The old address still works so bookmarks do not
    # break; it is the only thing left at this route.
    path('settings/', RedirectView.as_view(pattern_name='it_integrations',
                                          permanent=False),
         name='global_settings'),
]
