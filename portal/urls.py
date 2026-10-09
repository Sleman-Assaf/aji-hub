# portal/urls.py
from django.urls import path
from . import libraries, views

urlpatterns = [
    path('', views.policies, name='policies'),
    # Controlled documents. Every one of these checks the reader before it
    # answers — the file's own /media/ URL is never given out for a restricted
    # document, because that path is served with no check at all.
    path('doc/<int:pk>/',                  views.policy_document,  name='policy_document'),
    path('doc/<int:pk>/page/<int:index>/', views.policy_doc_page,  name='policy_doc_page'),
    path('doc/<int:pk>/file/',             views.policy_doc_file,  name='policy_doc_file'),

    # The same three endpoints for everything that is not a policy — the
    # handbook and the brand files. Mounted here rather than under each app's
    # own prefix so there is one document route to reason about.
    path('document/<slug:source>/<int:pk>/',                  views.document_meta, name='document_meta'),
    path('document/<slug:source>/<int:pk>/page/<int:index>/', views.document_page, name='document_page'),
    path('document/<slug:source>/<int:pk>/file/',             views.document_file, name='document_file'),
    # Management — Policies group only
    # ── running a document library ──
    # One screen: the libraries you run, and for each one its folders, its
    # documents and its settings together. /manage/ and the separate settings
    # page are kept only as redirects into it, so a bookmark still lands
    # somewhere that works.
    path('libraries/',                  libraries.libraries_home,       name='libraries_home'),
    path('libraries/new/',              libraries.library_new,          name='library_new'),
    path('libraries/<int:pk>/',         libraries.library_workspace,    name='library_workspace'),
    path('libraries/<int:pk>/settings/', libraries.library_settings_save, name='library_settings_save'),
    path('libraries/<int:pk>/remove/',  libraries.library_remove,       name='library_remove'),
    path('libraries/<int:pk>/import/',  libraries.library_import,       name='library_import'),
    path('libraries/<int:pk>/users/',   libraries.library_users,        name='library_users'),
    path('libraries/<int:pk>/users/set/', libraries.library_user_set,   name='library_user_set'),
    path('libraries/document/<int:pk>/save/',   libraries.document_save,   name='library_document_save'),
    path('libraries/document/<int:pk>/remove/', libraries.document_remove, name='library_document_remove'),
    path('manage/',                            views.policies_manage,        name='policies_manage'),
    path('manage/category/save/',              views.policy_category_save,   name='policy_category_save'),
    path('manage/category/<int:pk>/delete/',   views.policy_category_delete, name='policy_category_delete'),
    # One library's own settings page. Reachable from inside the library and
    # from the management list, and only by whoever runs it.
    path('library/<int:pk>/settings/', views.policy_library_settings, name='policy_library_settings'),
    path('library/people/',            views.policy_people_search,    name='policy_people_search'),
    path('library/<int:pk>/request/',  views.policy_access_request,   name='policy_access_request'),
    path('library/request/<int:pk>/decide/', views.policy_request_decide,
         name='policy_request_decide'),
    path('library/<int:pk>/folder/add/',    views.policy_folder_add,    name='policy_folder_add'),
    path('library/<int:pk>/upload/',        views.policy_files_upload,  name='policy_files_upload'),
    path('folder/<int:pk>/rename/',         views.policy_folder_rename, name='policy_folder_rename'),
    path('folder/<int:pk>/move/',           views.policy_folder_move,   name='policy_folder_move'),
    path('folder/<int:pk>/delete/',         views.policy_folder_delete, name='policy_folder_delete'),
    path('library/<int:pk>/claim/',    views.policy_library_claim,    name='policy_library_claim'),
    path('document/<int:pk>/access/',  views.policy_access_save,      name='policy_access_save'),
    path('manage/category/<int:category_pk>/policy/add/', views.policy_save, name='policy_save'),
    path('manage/policy/<int:pk>/edit/',       views.policy_edit,            name='policy_edit'),
    path('manage/policy/<int:pk>/delete/',     views.policy_delete,          name='policy_delete'),
]
