"""Management command to expire stale RxDjango caches.

Scans all registered ContextChannel classes for hot caches that have exceeded
their configured TTL and transitions them through COOLING to COLD.

Usage::

    # Expire all stale caches
    python manage.py expire_rxdjango_cache

    # Preview what would be expired without making changes
    python manage.py expire_rxdjango_cache --dry-run

This command is idempotent and safe to run concurrently (atomic Lua scripts
prevent double transitions). Schedule it via cron or Celery beat::

    # cron - run every 5 minutes
    */5 * * * * cd /path/to/project && python manage.py expire_rxdjango_cache

    # Celery beat
    CELERY_BEAT_SCHEDULE = {
        'expire-rx-caches': {
            'task': 'myapp.tasks.expire_rx_caches',
            'schedule': 300,
        },
    }
"""

import asyncio
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Expire stale RxDjango caches that have exceeded their TTL'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Scan and report stale caches without expiring them',
        )

    def handle(self, *args, **options):
        if options['dry_run']:
            asyncio.run(self._dry_run())
        else:
            asyncio.run(self._expire())

    async def _expire(self):
        from rxdjango.cache_expiry import expire_caches

        expired = await expire_caches()
        if expired:
            for channel_name, anchor_id in expired:
                self.stdout.write(
                    self.style.SUCCESS(f'Expired {channel_name} anchor {anchor_id}')
                )
            self.stdout.write(
                self.style.SUCCESS(f'\nTotal: {len(expired)} cache(s) expired')
            )
        else:
            self.stdout.write('No stale caches found')

    async def _dry_run(self):
        from rxdjango.cache_expiry import scan_stale_anchors

        stale = await scan_stale_anchors()
        if stale:
            for channel_name, anchor_id, state in stale:
                self.stdout.write(f'  {channel_name} anchor {anchor_id} ({state})')
            self.stdout.write(
                f'\nTotal: {len(stale)} stale cache(s) would be expired'
            )
        else:
            self.stdout.write('No stale caches found')
