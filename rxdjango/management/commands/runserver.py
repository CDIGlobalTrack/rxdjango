from daphne.management.commands.runserver import Command as RunserverCommand
from rxdjango.sdk import make_sdk

class Command(RunserverCommand):

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument('--makefrontend', action='store_true',
                            help="Build the frontend SDK files")

    def inner_run(self, *args, **kwargs):
        if kwargs['makefrontend']:
            make_sdk()
        super().inner_run(*args, **kwargs)
