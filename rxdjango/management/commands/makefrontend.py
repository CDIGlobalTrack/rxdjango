import sys
from django.core.management.base import BaseCommand
from rxdjango.sdk import make_sdk

class Command(BaseCommand):
    help=("Build the frontend SDK files."
          "Exit code 0 means code is up to date,"
          "1 means changes were made, or could me made (in case of dry-run)")


    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help="Do not write any changes")

        parser.add_argument('--quiet', action='store_true',
                            help="Do not output logs")
        parser.add_argument('--force', '-f', action='store_true',
                            help="Rebuild all files regardless of changes")


    def handle(self, *args, **kwargs):
        changed = make_sdk(
            not kwargs['dry_run'],
            kwargs['quiet'],
            kwargs['force'],
        )
        sys.exit(1 if changed else 0)
