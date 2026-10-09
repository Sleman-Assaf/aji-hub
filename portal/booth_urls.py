# portal/booth_urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.booth_wall, name='booth_wall'),
    path('album/<int:pk>/', views.booth_album_view, name='booth_album_view'),
    path('manage/', views.booth_manage, name='booth_manage'),
    path('manage/save/', views.booth_album_save, name='booth_album_save'),
    path('manage/<int:pk>/delete/', views.booth_album_delete, name='booth_album_delete'),
    path('manage/<int:pk>/photos/', views.booth_album_photos, name='booth_album_photos'),
]
