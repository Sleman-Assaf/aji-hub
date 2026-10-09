from django.apps import AppConfig


class CmsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'cms'
    verbose_name = 'Hub Content'

    def ready(self):
        """Watch every field that can hold an icon name.

        The set of icons sent to the browser is "the ones actually in use", which
        is worked out by scanning those fields and then cached. Nothing used to
        notice when one of them changed, so an icon chosen for a community or a
        policy category never reached the browser's copy — see
        icon_models.watch_icon_fields.
        """
        from .icon_models import watch_icon_fields
        watch_icon_fields()
