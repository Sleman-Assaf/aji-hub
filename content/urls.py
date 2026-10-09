# content/urls.py  (news)
from django.urls import path
from . import views

urlpatterns = [
    path('', views.news_list, name='news_list'),
    # 'new/' must stay above the <slug> route so it isn't read as a slug.
    path('new/', views.news_create, name='news_create'),
    path('comment/<int:pk>/edit/', views.news_comment_edit, name='news_comment_edit'),
    path('comment/<int:pk>/delete/', views.news_comment_delete, name='news_comment_delete'),
    path('<slug:slug>/edit/', views.news_edit, name='news_edit'),
    path('<slug:slug>/delete/', views.news_delete, name='news_delete'),
    path('<slug:slug>/', views.news_detail, name='news_detail'),
]
