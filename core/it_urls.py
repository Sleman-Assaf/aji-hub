# core/it_urls.py
"""The IT portal's routes. Mounted at /it/ — see aji_hub/urls.py."""
from django.urls import path

from . import it_email_views as e
from . import it_views as v
from . import views as core_views
from messaging import it_call_views as calls

urlpatterns = [
    path('',                          v.it_home,             name='it_home'),

    path('roles/',                    v.it_roles,            name='it_roles'),
    path('roles/new/',                v.it_role_create,      name='it_role_create'),
    path('roles/<int:pk>/',           v.it_role_detail,      name='it_role_detail'),
    path('roles/<int:pk>/rename/',    v.it_role_rename,      name='it_role_rename'),
    path('roles/<int:pk>/delete/',    v.it_role_delete,      name='it_role_delete'),
    path('roles/<int:pk>/permissions/', v.it_role_permissions, name='it_role_permissions'),
    path('roles/<int:pk>/add/',       v.it_role_add_member,  name='it_role_add_member'),
    path('roles/<int:pk>/remove/<int:user_pk>/',
         v.it_role_remove_member,     name='it_role_remove_member'),

    path('users/',                    v.it_users,            name='it_users'),
    path('users/<int:pk>/',           v.it_user_detail,      name='it_user_detail'),
    path('users/<int:pk>/save/',      v.it_user_save,        name='it_user_save'),
    path('users/<int:pk>/toggle/',    v.it_user_toggle,      name='it_user_toggle'),

    path('directory/',                v.it_directory,        name='it_directory'),
    path('directory/import/',         v.it_directory_import, name='it_directory_import'),

    path('backups/',                  v.it_backups,          name='it_backups'),
    path('backups/now/',              v.it_backup_now,       name='it_backup_now'),
    path('backups/folder/',           v.it_backup_folder,    name='it_backup_folder'),
    path('backups/<int:pk>/download/', v.it_backup_download, name='it_backup_download'),
    path('backups/<int:pk>/restore/', v.it_backup_restore,  name='it_backup_restore'),
    path('backups/<int:pk>/delete/',  v.it_backup_delete,    name='it_backup_delete'),
    path('backups/settings/',         v.it_backup_settings,  name='it_backup_settings'),
    path('backups/report/',           v.it_report_now,       name='it_report_now'),
    path('backups/report/<str:name>/', v.it_report_download, name='it_report_download'),

    # The settings screens themselves, unchanged -- only where they are
    # rendered has moved. See core.views.global_settings.
    path('integrations/',             core_views.global_settings,
         name='it_integrations'),

    # The wording of every email the Hub sends. `code` is a registry key such
    # as athar.period_open, so the pattern has to allow the dot.
    path('emails/',                   e.it_emails,           name='it_emails'),
    path('emails/<str:code>/',        e.it_email_detail,     name='it_email_detail'),
    path('emails/<str:code>/save/',   e.it_email_save,       name='it_email_save'),
    path('emails/<str:code>/toggle/', e.it_email_toggle,     name='it_email_toggle'),
    path('emails/<str:code>/reset/',  e.it_email_reset,      name='it_email_reset'),
    path('emails/<str:code>/test/',   e.it_email_test,       name='it_email_test'),

    # Live call statistics and bandwidth — reads the media bridge's /stats
    # and the call log. The page polls its own data endpoint.
    path('calls/',                    calls.it_calls,        name='it_calls'),
    path('calls/data/',               calls.it_calls_data,   name='it_calls_data'),
    path('calls/report/',             calls.it_calls_report, name='it_calls_report'),
    path('calls/settings/',           calls.it_call_settings, name='it_call_settings'),

    path('logs/',                     v.it_logs,             name='it_logs'),
    path('security/',                 v.it_security,         name='it_security'),
    path('people/',                   v.it_people_search,    name='it_people_search'),
]
