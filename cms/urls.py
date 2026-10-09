# cms/urls.py
from django.urls import path
from . import edit_views, icon_views, media_views, nav_views, views

urlpatterns = [
    path('', views.page_index, name='cms_index'),

    # ── The icon library. Above <slug> so "icons" is never read as a page slug.
    # The media library. A picker only — no navigation entry.
    path('media/', media_views.media_list, name='cms_media'),
    path('media/upload/', media_views.media_upload, name='cms_media_upload'),
    path('media/<int:pk>/update/', media_views.media_update,
         name='cms_media_update'),
    # Cropping and resizing. Non-destructive: the previous file is kept as a
    # version, which is what restore/ puts back.
    path('media/<int:pk>/transform/', media_views.media_transform,
         name='cms_media_transform'),
    path('media/<int:pk>/restore/', media_views.media_restore,
         name='cms_media_restore'),

    # The navigation and the logo. Above <slug> so they are never read as
    # a page slug.
    path('navigation/', nav_views.nav_manage, name='cms_nav'),
    path('navigation/new/', nav_views.nav_save, name='cms_nav_create'),
    path('navigation/<int:pk>/save/', nav_views.nav_save, name='cms_nav_save'),
    path('navigation/<int:pk>/delete/', nav_views.nav_delete, name='cms_nav_delete'),
    path('navigation/order/', nav_views.nav_reorder, name='cms_nav_reorder'),
    path('branding/', nav_views.branding_save, name='cms_branding_save'),
    path('<slug:slug>/in-nav/', nav_views.page_nav_toggle,
         name='cms_page_nav_toggle'),

    # The icon set as a cacheable script. Above the library so "icons.js" is
    # never read as a page slug.
    path('icons.js', icon_views.icons_js, name='cms_icons_js'),
    path('icons/', icon_views.icon_library, name='cms_icons'),
    path('icons/pick/', icon_views.icon_picker, name='cms_icon_picker'),
    path('icons/new/', icon_views.icon_save, name='cms_icon_create'),
    path('icons/<int:pk>/save/', icon_views.icon_save, name='cms_icon_save'),
    path('icons/<int:pk>/reset/', icon_views.icon_reset, name='cms_icon_reset'),
    path('icons/<int:pk>/delete/', icon_views.icon_delete, name='cms_icon_delete'),


    # ── On-page editor. All POST, all JSON, so editing never reloads the page
    # being edited. Kept above <slug> so none of them can be read as a slug.
    path('edit/block/<int:pk>/inline/', edit_views.inline_save, name='cms_inline_save'),
    path('edit/item/<int:pk>/inline/', edit_views.item_inline_save,
         name='cms_item_inline_save'),
    path('edit/block/<int:pk>/settings/', edit_views.block_settings,
         name='cms_block_settings'),
    path('edit/block/<int:pk>/update/', edit_views.block_update, name='cms_block_update'),
    path('edit/block/<int:pk>/<str:action>/', edit_views.block_action,
         name='cms_block_action'),
    path('edit/<slug:slug>/block/new/', edit_views.block_create, name='cms_block_create'),
    # A ready-made layout: the row and its contents in one insert.
    path('edit/<slug:slug>/preset/new/', edit_views.preset_create,
         name='cms_preset_create'),
    path('edit/section/<int:section_pk>/item/new/', edit_views.item_create,
         name='cms_item_quick_create'),
    path('edit/section/<int:section_pk>/items/order/', edit_views.item_reorder,
         name='cms_item_reorder'),
    path('edit/item/<int:pk>/delete/', edit_views.item_delete,
         name='cms_item_quick_delete'),

    # Static routes stay above <slug> so they are never read as a page slug.
    # Straight into the editor. The old settings-form-first route is kept at
    # new/settings/ for the rare case of setting up a page without editing it.
    path('new/', edit_views.page_start, name='cms_page_create'),
    path('new/settings/', views.page_settings, name='cms_page_create_form'),
    path('bind/', views.bind_path, name='cms_bind_path'),
    path('<slug:slug>/', views.page_detail, name='cms_page'),
    # Drafts. Editing works on a copy; these put it live or throw it away.
    path('<slug:slug>/publish/', edit_views.draft_publish, name='cms_draft_publish'),
    path('<slug:slug>/discard/', edit_views.draft_discard, name='cms_draft_discard'),
    path('<slug:slug>/draft/', edit_views.draft_status, name='cms_draft_status'),
    # The page's structure, for the sidebar's Layers tab.
    path('<slug:slug>/layers/', edit_views.page_layers, name='cms_page_layers'),
    path('<slug:slug>/manage/', views.page_manage, name='cms_page_manage'),
    path('<slug:slug>/settings/', views.page_settings, name='cms_page_settings'),
    path('<slug:slug>/reorder/', views.sections_reorder, name='cms_sections_reorder'),

    path('<slug:slug>/section/new/', views.section_form, name='cms_section_create'),
    path('<slug:slug>/section/<int:pk>/', views.section_form, name='cms_section_edit'),
    path('<slug:slug>/section/<int:pk>/<str:action>/', views.section_action,
         name='cms_section_action'),

    path('<slug:slug>/section/<int:section_pk>/item/new/', views.item_form,
         name='cms_item_create'),
    path('<slug:slug>/section/<int:section_pk>/item/<int:pk>/', views.item_form,
         name='cms_item_edit'),
    path('<slug:slug>/section/<int:section_pk>/item/<int:pk>/<str:action>/',
         views.item_action, name='cms_item_action'),
]
