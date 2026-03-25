import importlib
from urllib.parse import urlsplit

from django.conf import settings
from django.apps import apps, AppConfig
from django.core.checks import Warning, register


def _url_has_credentials(url):
    if not url:
        return True

    parsed = urlsplit(url)
    return any(
        value not in (None, '')
        for value in (parsed.username, parsed.password)
    )


@register()
def check_service_auth(app_configs, **kwargs):
    warnings = []

    if not _url_has_credentials(getattr(settings, 'MONGO_URL', '')):
        warnings.append(Warning(
            'MONGO_URL does not appear to include authentication credentials.',
            hint=(
                'Use an authenticated MongoDB URL in production, for example '
                "'mongodb://app-user:strong-password@db.example.com:27017/"
                "?authSource=admin'."
            ),
            id='rxdjango.W001',
        ))

    if not _url_has_credentials(getattr(settings, 'REDIS_URL', '')):
        warnings.append(Warning(
            'REDIS_URL does not appear to include authentication credentials.',
            hint=(
                'Use an authenticated Redis URL in production, for example '
                "'redis://:strong-password@redis.example.com:6379/0'."
            ),
            id='rxdjango.W002',
        ))

    return warnings


class RxDjangoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'rxdjango'

    def ready(self):
        """Discover and register ContextChannel subclasses within Django apps."""
        from . import channels

        for app_config in apps.get_app_configs():
            try:
                # Attempt to import the channels.py module from the app
                channels_module = importlib.import_module(f"{app_config.name}.channels")

                # Check for subclasses of ContextChannel in the module
                for attr_name in dir(channels_module):
                    attr = getattr(channels_module, attr_name)
                    # Register the subclass in the global dictionary
                    if not isinstance(attr, type) or \
                       not issubclass(attr, channels.ContextChannel) or \
                       attr.Meta.abstract:
                        continue
                    attr._signal_handler.setup(app_config)

            except ImportError:
                # channels.py not found in the app, so just continue
                pass
