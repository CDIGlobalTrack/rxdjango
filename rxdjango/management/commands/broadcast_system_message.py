import sys
import asyncio
from django.core.management.base import BaseCommand
from rxdjango.sdk import make_sdk
from rxdjango.websocket_router import send_system_message


class Command(BaseCommand):
    help="Broadcast a system message to all connected clients"

    def add_arguments(self, parser):
        parser.add_argument('source',
                            type=str,
                            help=(
                                'Source of the broadcast message'
                                '(currently only "maintenance" is supported)'
                            ))

        parser.add_argument('message',
                            type=str,
                            help='Verbose message to users',
                            )

    def handle(self, *args, **options):
        asyncio.run(
            send_system_message(
                options['source'],
                options.get('message'),
            )
        )
