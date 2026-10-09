from django.urls import path

from . import (cert_views, employee_views, feed_views, hr_views,
               manager_views, praise_views, release_views, views)

urlpatterns = [
    path('',                    views.home,              name='taqdeer_home'),
    path('kudos/',             views.praise_new,        name='taqdeer_praise_new'),
    path('kudos/<int:pk>/hide/', views.praise_hide,     name='taqdeer_praise_hide'),
    path('kudos/<int:pk>/edit/', feed_views.praise_edit, name='taqdeer_praise_edit'),
    path('manage/',             views.manage,            name='taqdeer_manage'),
    path('manage/cycle/',       views.cycle_action,      name='taqdeer_cycle_action'),
    path('manage/<int:pk>/',    views.nomination_update, name='taqdeer_nomination_update'),
    path('certificate/<int:pk>/', release_views.certificate, name='taqdeer_certificate'),
    path('people/',             manager_views.people,    name='taqdeer_people'),
    path('people/all/',         hr_views.people_all,     name='taqdeer_people_all'),
    path('mine/',               employee_views.my_recognition, name='taqdeer_mine'),
    path('post/<str:kind>/<int:pk>/', feed_views.post, name='taqdeer_post'),
    path('feed/more/',          views.feed_more,         name='taqdeer_feed_more'),
    path('like/<str:kind>/<int:pk>/', feed_views.like, name='taqdeer_like'),
    path('comment/<int:pk>/edit/',   feed_views.comment_edit,   name='taqdeer_comment_edit'),
    path('comment/<int:pk>/delete/', feed_views.comment_delete, name='taqdeer_comment_delete'),
    path('archive/',            employee_views.archive,  name='taqdeer_archive'),
    path('kudos/queue/',       praise_views.queue,      name='taqdeer_praise_queue'),
    path('kudos/<int:pk>/decide/', praise_views.decide,  name='taqdeer_praise_decide'),

    # -- manager --
    path('submissions/',        manager_views.submissions,  name='taqdeer_submissions'),
    # POST only — this is the submit action, not a page. Anything a person
    # clicks (a notification, an email, a redirect) wants taqdeer_submissions,
    # which is the page the form lives on. Pointed here, it 405s.
    path('nominate/',           manager_views.nominate,     name='taqdeer_nominate'),
    path('nominate/<int:pk>/',  manager_views.resubmit,     name='taqdeer_resubmit'),
    path('person/',             manager_views.person_card,  name='taqdeer_person_card'),

    # ── HR ──
    path('athar/',              hr_views.athar_manage,   name='taqdeer_athar_manage'),
    path('athar/<int:pk>/',     hr_views.athar_period,   name='taqdeer_athar_period'),
    path('athar/<int:pk>/delete/', hr_views.athar_delete, name='taqdeer_athar_delete'),
    path('settings/',           hr_views.settings_page,  name='taqdeer_settings'),
    path('athar/<int:pk>/release/', release_views.release, name='taqdeer_release'),
    path('preview/<int:pk>/',   release_views.preview,   name='taqdeer_preview'),
    path('certificate/<int:pk>/image/', release_views.certificate_image,
         name='taqdeer_certificate_image'),
    path('certifications/',     cert_views.manage,       name='taqdeer_certifications'),
    path('certifications/<int:pk>/', cert_views.update,  name='taqdeer_certification_update'),
    path('certifications/<int:pk>/image/', cert_views.image,
         name='taqdeer_certification_image'),
]
