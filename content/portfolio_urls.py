# content/portfolio_urls.py
from django.urls import path
from . import views
urlpatterns = [
    # The projects behind this page. Static routes stay above any <slug>.
    path('manage/', views.portfolio_manage, name='portfolio_manage'),
    path('manage/save/', views.project_save, name='project_save'),
    path('manage/<int:pk>/delete/', views.project_delete, name='project_delete'),
    # Bulk edit by spreadsheet: download the template, upload it, confirm.
    path('manage/template/', views.project_template, name='project_template'),
    path('manage/upload/', views.project_bulk_upload, name='project_bulk_upload'),
    path('manage/upload/apply/', views.project_bulk_apply, name='project_bulk_apply'),
    path('', views.portfolio, name='portfolio'),
]
