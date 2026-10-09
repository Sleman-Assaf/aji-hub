# training/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.training_list, name='training_list'),
    path('submit/', views.training_submit, name='training_submit'),
    path('<int:pk>/', views.training_detail, name='training_detail'),
    path('<int:pk>/<str:action>/', views.training_action, name='training_action'),
]
