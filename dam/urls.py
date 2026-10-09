# dam/urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.space_list, name='dam_spaces'),
    path('<slug:slug>/', views.browse, name='dam_space'),
    path('<slug:slug>/f/<int:folder_pk>/', views.browse, name='dam_folder'),
    path('<slug:slug>/a/<int:asset_pk>/', views.asset_detail, name='dam_asset'),

    # Operations — all POST, all return JSON so the grid refreshes in place.
    path('<slug:slug>/folder/new/', views.folder_create, name='dam_folder_create'),
    path('<slug:slug>/folder/<int:folder_pk>/update/', views.folder_update,
         name='dam_folder_update'),
    path('<slug:slug>/upload/', views.asset_upload, name='dam_upload'),
    path('<slug:slug>/asset/<int:asset_pk>/update/', views.asset_update,
         name='dam_asset_update'),
    path('<slug:slug>/bulk/', views.asset_bulk, name='dam_bulk'),
]
