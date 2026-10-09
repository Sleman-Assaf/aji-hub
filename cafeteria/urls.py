# cafeteria/urls.py
from django.urls import path

from . import views

app_name = 'cafeteria'

urlpatterns = [
    path('', views.cafeteria_home, name='home'),
    path('order/', views.place_order, name='place_order'),
    path('staff/', views.staff_dashboard, name='staff_dashboard'),
    path('staff/order/<int:pk>/status/', views.order_set_status, name='order_set_status'),
    path('staff/package-order/<int:pk>/status/', views.package_order_set_status, name='package_order_set_status'),
    path('staff/item/save/', views.item_save, name='item_save'),
    path('staff/package/save/', views.package_save, name='package_save'),
    path('packages/', views.package_order, name='package_order'),
    path('catering/<int:pk>/review/', views.catering_review, name='catering_review'),
]
