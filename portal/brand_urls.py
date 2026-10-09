# portal/brand_urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('',                                        views.brand_files,          name='brand_files'),
    # Management — Branding group only
    path('manage/',                                 views.brand_manage,         name='brand_manage'),
    # Sections / templates / files
    path('manage/section/add/',                     views.brand_section_add,    name='brand_section_add'),
    path('manage/section/<int:pk>/delete/',         views.brand_section_delete, name='brand_section_delete'),
    path('manage/section/<int:section_pk>/template/add/', views.brand_template_add, name='brand_template_add'),
    path('manage/template/<int:pk>/delete/',        views.brand_template_delete, name='brand_template_delete'),
    path('manage/template/<int:template_pk>/upload/', views.brand_file_upload,  name='brand_file_upload'),
    path('manage/template/<int:template_pk>/group/add/', views.brand_group_add, name='brand_group_add'),
    path('manage/group/<int:pk>/delete/',           views.brand_group_delete,   name='brand_group_delete'),
    path('manage/file/<int:pk>/delete/',            views.brand_file_delete,    name='brand_file_delete'),
    # Colours
    path('manage/colour/save/',                     views.brand_colour_save,    name='brand_colour_save'),
    path('manage/colour/<int:pk>/delete/',          views.brand_colour_delete,  name='brand_colour_delete'),
    # Who can see what
    path('manage/visibility/',                      views.brand_visibility_save, name='brand_visibility_save'),
    path('manage/section/<int:pk>/access/',         views.brand_section_access,  name='brand_section_access'),
    path('manage/template/<int:pk>/access/',        views.brand_template_access, name='brand_template_access'),
    path('manage/people/',                          views.brand_people_search,   name='brand_people_search'),
    # Download — all logged-in users
    path('download/<int:pk>/',                      views.brand_file_download,  name='brand_file_download'),
]
