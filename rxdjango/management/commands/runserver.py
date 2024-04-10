from django.apps import apps
from django.conf import settings
from daphne.management.commands.runserver import Command as RunserverCommand
from rxdjango.ts.interfaces import create_app_interfaces
from rxdjango.ts.channels import create_app_channels


class Command(RunserverCommand):

    def inner_run(self, *args, **kwargs):
        self._make_sdk()
        super().inner_run(*args, **kwargs)

    def _make_sdk(self):
        print("Generating RxDjango SDK")
        self._check()

        models = apps.get_models()
        installed_apps = list(set([ x.__module__.split('.')[0] for x in models]))

        for app in installed_apps:
            create_app_interfaces(app)
            create_app_channels(app)

    def _check(self):
        if not getattr(settings, 'RX_FRONTEND_DIR', None):
            raise ProgrammingError(
                "settings.RX_FRONTEND_DIR is not set. Configure it with a folder "
                "inside your react application."
            )
