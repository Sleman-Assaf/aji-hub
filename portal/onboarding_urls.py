# portal/onboarding_urls.py
from django.urls import path
from . import views

urlpatterns = [
    path('', views.onboarding, name='onboarding'),
    # HR content management — HR group only
    path('manage/',                          views.hr_content_manage,       name='hr_content_manage'),
    path('manage/item/save/',                views.onboarding_item_save,    name='onboarding_item_save'),
    path('manage/item/<int:pk>/delete/',     views.onboarding_item_delete,  name='onboarding_item_delete'),
    path('manage/wellness/save/',            views.wellness_item_save,      name='wellness_item_save'),
    path('manage/wellness/<int:pk>/delete/', views.wellness_item_delete,    name='wellness_item_delete'),
    path('manage/childcare/save/',           views.childcare_partner_save,  name='childcare_partner_save'),
    path('manage/childcare/<int:pk>/delete/', views.childcare_partner_delete, name='childcare_partner_delete'),
    path('manage/cert/save/',                views.certification_save,      name='certification_save'),
    path('manage/cert/<int:pk>/delete/',     views.certification_delete,    name='certification_delete'),
    path('manage/explore-tab/save/',         views.explore_tab_save,        name='explore_tab_save'),
    path('manage/ceo-letter/save/',          views.ceo_letter_save,         name='ceo_letter_save'),
    path('manage/handbook-file/save/',       views.handbook_file_save,      name='handbook_file_save'),
    # Editing the handbook's wording straight on the page.
    path('handbook/inline/',                 views.handbook_inline_save,    name='handbook_inline_save'),
    # Adding, renaming and removing a certification without leaving the page.
    path('certifications/inline/',           views.certification_inline,    name='certification_inline'),
    # Editing the CEO letter's wording straight on the page.
    path('ceo-letter/inline/',               views.ceo_inline_save,         name='ceo_inline_save'),
    # WhatsApp channels. Previously Django-admin only, which needs a staff
    # account that neither HR nor an Intranet Admin has.
    path('manage/whatsapp/save/',            views.whatsapp_channel_save,   name='whatsapp_channel_save'),
    path('manage/whatsapp/<int:pk>/delete/', views.whatsapp_channel_delete, name='whatsapp_channel_delete'),
]
