from django.core.management.base import BaseCommand
from rxdjango.sdk import make_sdk

class Command(BaseCommand):
    help="Build the frontend SDK files"

    def handle(self, *args, **kwargs):
        make_sdk()
