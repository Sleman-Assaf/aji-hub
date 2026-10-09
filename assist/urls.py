# assist/urls.py
from django.urls import path
from . import views
from . import export_views

urlpatterns = [
    path('',                        views.assist_widget,    name='assist_widget'),
    path('new/',                    views.assist_new,       name='assist_new'),
    path('ask/',                    views.ask,              name='assist_ask'),
    path('history/',                views.history,          name='assist_history'),
    path('history/clear/',          views.clear_history,    name='assist_clear_history'),
    path('session/<int:session_pk>/', views.session_messages, name='assist_session'),

    path('message/<int:msg_pk>/export/table.xlsx', export_views.export_message_table_xlsx, name='assist_export_message_xlsx'),
    path('session/<int:session_pk>/export/docx',  export_views.export_session_docx, name='assist_export_session_docx'),
    path('session/<int:session_pk>/export/pdf',   export_views.export_session_pdf,  name='assist_export_session_pdf'),
    path('session/<int:session_pk>/export/xlsx',  export_views.export_session_xlsx, name='assist_export_session_xlsx'),
]
