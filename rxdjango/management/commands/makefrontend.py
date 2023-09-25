from django.apps import apps
from django.db import ProgrammingError
from django.core.management.base import BaseCommand
from django.conf import settings
from rxdjango.ts.interfaces import create_app_interfaces
from rxdjango.ts.channels import create_app_channels


class Command(BaseCommand):
    help = 'Generate typescript interfaces and classes'

    def add_arguments(self, parser):
        parser.add_argument('app', nargs='*', type=str)

    def handle(self, *args, **options):
        self._check()

        all_apps = options['app']

        if not all_apps:
            models = apps.get_models()
            all_apps = list(set([ x.__module__.split('.')[0] for x in models]))

        for app in all_apps:
            create_app_interfaces(app)
            create_app_channels(app)

    def _check(self):
        if not getattr(settings, 'RX_FRONTEND_DIR', None):
            raise ProgrammingError(
                "settings.RX_FRONTEND_DIR is not set. Configure it with a folder "
                "inside your react application."
            )
