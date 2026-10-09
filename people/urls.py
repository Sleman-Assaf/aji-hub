# people/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.directory, name='directory'),
    path('<int:pk>/', views.profile_detail, name='profile_detail'),
    path('<int:pk>/reporting-line/', views.reporting_line_api, name='reporting_line_api'),
    path('<int:pk>/modal/', views.profile_modal, name='profile_modal'),
    path('skills/', views.skills_directory, name='skills_directory'),
    path('extensions/', views.phone_directory, name='phone_directory'),
    path('org/', views.org_chart, name='org_chart'),
    path('org/search/', views.org_search, name='org_search'),
    path('org/<int:pk>/node/', views.org_node_partial, name='org_node_partial'),
    path('me/',      views.my_profile,  name='my_profile'),
    path('me/edit/', views.profile_edit, name='profile_edit'),
    path('api/search/', views.api_people_search, name='api_people_search'),
    path('qr/', views.qr_codes, name='qr_codes'),
]
