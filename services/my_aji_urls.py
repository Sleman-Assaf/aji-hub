# services/my_aji_urls.py
from django.urls import path

from . import views

urlpatterns = [
    path('', views.my_aji, name='my_aji'),
    path('category/<int:pk>/', views.service_category_detail, name='my_aji_category'),
    path('service/<int:pk>/', views.service_detail, name='my_aji_service'),
]
