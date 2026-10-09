# aji_hub/urls.py
from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings

urlpatterns = [
    path('admin/', admin.site.urls),
    path('auth/', include('core.auth_urls')),
    path('it/', include('core.it_urls')),
    path('', include('core.urls')),
    path('people/', include('people.urls')),
    path('news/', include('content.urls')),
    path('calendar/', include('content.cal_urls')),
    path('exchange/', include('exchange.urls')),
    path('policies/', include('portal.urls')),
    path('brand/', include('portal.brand_urls')),
    path('booth/', include('portal.booth_urls')),
    path('onboarding/', include('portal.onboarding_urls')),
    path('voice/', include('voice.urls')),
    path('messaging/', include('messaging.urls')),
    path('assist/', include('assist.urls')),
    path('portfolio/', include('content.portfolio_urls')),
    path('people-culture/', include('content.pc_urls')),
    path('services/', include('services.urls')),
    path('my-aji/', include('services.my_aji_urls')),
    path('cafeteria/', include('cafeteria.urls')),
    path('p/', include('cms.urls')),
    path('training/', include('training.urls')),
    path('taqdeer/', include('taqdeer.urls')),
    # The /library/ route is deliberately not mounted. AJi Booth and Brand Files
    # remain the file destinations; the dam app stays on disk unreferenced so its
    # versioning and PDF-preview work is available if it is ever wanted, but it is
    # reachable from nowhere in the site.
]

# `static()` is a no-op when DEBUG is off, so this does the same job in a way
# that survives a deployment. See SERVE_MEDIA in settings.
if settings.SERVE_MEDIA:
    from django.views.static import serve as _serve_media
    urlpatterns += [
        re_path(r'^%s(?P<path>.*)$' % settings.MEDIA_URL.lstrip('/'),
                _serve_media, {'document_root': settings.MEDIA_ROOT}),
    ]
