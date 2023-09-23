import importlib
from django.apps import apps, AppConfig


class ReactFrameworkConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'rxdjango'

    def ready(self):
        """Discover and register StateChannel subclasses within Django apps."""
        from . import channels

        for app_config in apps.get_app_configs():
            try:
                # Attempt to import the channels.py module from the app
                channels_module = importlib.import_module(f"{app_config.name}.channels")

                # Check for subclasses of StateChannel in the module
                for attr_name in dir(channels_module):
                    attr = getattr(channels_module, attr_name)
                    # Register the subclass in the global dictionary
                    if not isinstance(attr, type) or \
                       not issubclass(attr, channels.StateChannel) or \
                       attr.Meta.abstract:
                        continue
                    attr._signal_handler.setup(app_config)

            except ImportError:
                # channels.py not found in the app, so just continue
                pass
