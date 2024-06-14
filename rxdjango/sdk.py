from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from rxdjango.ts.interfaces import create_app_interfaces
from rxdjango.ts.channels import create_app_channels


def make_sdk():
    print("Generating RxDjango SDK")
    check()

    models = apps.get_models()
    installed_apps = list(set([ x.__module__.split('.')[0] for x in models]))

    for app in installed_apps:
        create_app_interfaces(app)
        create_app_channels(app)


def check():
    if not getattr(settings, 'RX_FRONTEND_DIR', None):
        raise ImproperlyConfigured(
            "settings.RX_FRONTEND_DIR is not set. Configure it with a folder "
            "inside your react application."
        )
