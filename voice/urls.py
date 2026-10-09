# voice/urls.py
from django.urls import path
from . import views
urlpatterns = [
    path('suggest/', views.suggestion_box, name='suggestion_box'),
    path('suggest/admin/', views.suggestion_admin, name='suggestion_admin'),
    path('survey/add/', views.add_survey, name='add_survey'),
]
