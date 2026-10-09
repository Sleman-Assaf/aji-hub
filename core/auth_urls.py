# core/auth_urls.py
from django.urls import path
from . import auth_views as hub_auth_views

urlpatterns = [
    path('login/',  hub_auth_views.login_view,  name='login'),
    path('logout/', hub_auth_views.logout_view, name='logout'),
]
