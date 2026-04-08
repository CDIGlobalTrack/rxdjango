"""Management command to clear RxDjango's MongoDB and Redis caches.

Clears all cached instance data from MongoDB and resets Redis state keys
for registered ContextChannel classes. Indexes are preserved and re-ensured
after clearing.

Use this command when:
- Deploying serializer schema changes (added/removed/renamed fields)
- Manually invalidating stale cache after data corrections
- As part of a blue/green deployment switch

The cache is self-healing: after clearing, the next client connection
triggers a full state rebuild from the ORM.

Usage::

    # Clear all channel caches
    python manage.py clear_rxdjango_cache

    # Clear a specific channel
    python manage.py clear_rxdjango_cache --channel myapp.MyChannel

    # Preview what would be cleared
    python manage.py clear_rxdjango_cache --dry-run
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Clear RxDjango MongoDB and Redis caches for all or specific channels'

    def add_arguments(self, parser):
        parser.add_argument(
            '--channel',
            type=str,
            help='Fully qualified channel class name (e.g. myapp.MyChannel). '
                 'If omitted, clears all registered channels.',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='List channels that would be cleared without clearing them.',
        )

    def handle(self, *args, **options):
        channels = self._resolve_channels(options.get('channel'))

        if options['dry_run']:
            self.stdout.write('Channels that would be cleared:')
            for channel_class in channels:
                self.stdout.write(f'  {channel_class.__module__}.{channel_class.__name__}')
            self.stdout.write(f'\nTotal: {len(channels)} channel(s)')
            return

        for channel_class in channels:
            name = f'{channel_class.__module__}.{channel_class.__name__}'

            from rxdjango.mongo import MongoSignalWriter
            from rxdjango.redis import RedisSession

            mongo = MongoSignalWriter(channel_class)
            mongo.clear_cache()
            mongo.ensure_indexes()

            RedisSession.init_database(channel_class)

            channel_class._state_model.clean_active()

            self.stdout.write(self.style.SUCCESS(f'Cleared {name}'))

        self.stdout.write(self.style.SUCCESS(
            f'\nTotal: {len(channels)} channel(s) cleared'
        ))

    def _resolve_channels(self, channel_name):
        """Resolve channel classes from registry, optionally filtered by name."""
        from rxdjango.channels import ContextChannel

        registry = ContextChannel.get_registered_channels()

        if not channel_name:
            return sorted(registry, key=lambda c: c.name)

        for channel_class in registry:
            qualified = f'{channel_class.__module__}.{channel_class.__name__}'
            simple = channel_class.__name__
            if channel_name in (qualified, simple):
                return [channel_class]

        available = ', '.join(
            f'{c.__module__}.{c.__name__}' for c in registry
        )
        raise CommandError(
            f'Channel "{channel_name}" not found. '
            f'Available: {available}'
        )
