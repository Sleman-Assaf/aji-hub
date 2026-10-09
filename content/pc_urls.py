# content/pc_urls.py
from django.urls import path
from . import views

from portal import views as portal_views

urlpatterns = [
    path('', views.people_culture, name='people_culture'),

    # This page's own content. It used to be reachable only from
    # /onboarding/manage/, so editing a wellness card or a WhatsApp channel meant
    # going to the Onboarding module to do it.
    path('manage/', portal_views.people_culture_manage, name='people_culture_manage'),
    path('post/', views.hr_post, name='hr_post'),

    # HR Information — frontend CMS. Static routes stay above <slug>.
    path('hr/', views.hr_manage, name='hr_manage'),
    path('hr/new/', views.hr_form, name='hr_create'),
    path('hr/<int:pk>/edit/', views.hr_form, name='hr_edit'),
    path('hr/<int:pk>/<str:action>/', views.hr_action, name='hr_action'),
    path('update/<slug:slug>/', views.hr_detail, name='hr_detail'),
]
