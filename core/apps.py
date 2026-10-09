from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        # Registers the project's own `manage.py check` rules. Imported here
        # because a check has to be registered before anything asks for one.
        from . import checks  # noqa: F401
        # Sign-ins and workflow moves into the audit log. Same reason: a
        # receiver has to be connected before the signal it listens for fires.
        from . import signals  # noqa: F401
        signals.connect()
