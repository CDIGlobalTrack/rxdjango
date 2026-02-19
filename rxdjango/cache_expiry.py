"""TTL-based cache expiry for RxDjango's MongoDB cache.

This module scans all registered ContextChannel classes and expires hot
caches that have exceeded their TTL. The actual COOLING cycle is implemented
by ``ContextChannel._cooling_cycle()``.

Usage::

    from rxdjango.cache_expiry import expire_caches

    # Expire all stale caches across all registered channels
    expired = await expire_caches()
"""

import logging
import redis as redis_lib
from django.conf import settings
from .redis import RedisStateSession

logger = logging.getLogger('rxdjango.cache_expiry')


async def expire_caches():
    """Expire hot caches past their TTL for all registered channels.

    Scans Redis for all anchors with state keys per channel. For each
    anchor whose cache has expired (sessions==0 and TTL elapsed), runs
    the COOLING cycle via the channel's ``_cooling_cycle()``.

    Returns:
        list of (channel_name, anchor_id) tuples that were expired.
    """
    from .channels import ContextChannel

    expired = []
    for channel_class in ContextChannel.get_registered_channels():
        ttl = channel_class.get_cache_ttl()

        anchor_ids = await _scan_anchor_ids(channel_class)
        for anchor_id in anchor_ids:
            try:
                redis_session = RedisStateSession(channel_class, anchor_id)
                if not await redis_session.start_cooling_if_stale(ttl):
                    continue

                logger.info(
                    'COOLING %s anchor %s', channel_class.__name__, anchor_id
                )
                await channel_class._cooling_cycle(anchor_id, redis_session)
                expired.append((channel_class.__name__, anchor_id))
            except Exception:
                logger.exception(
                    'Error expiring %s anchor %s',
                    channel_class.__name__, anchor_id,
                )

    return expired


async def scan_stale_anchors():
    """Scan for anchors that would be expired, without actually expiring them.

    Returns:
        list of (channel_name, anchor_id, state) tuples for HOT anchors past TTL.
    """
    from .channels import ContextChannel

    stale = []
    for channel_class in ContextChannel.get_registered_channels():
        ttl = channel_class.get_cache_ttl()

        anchor_ids = await _scan_anchor_ids(channel_class)
        for anchor_id in anchor_ids:
            redis_session = RedisStateSession(channel_class, anchor_id)
            await redis_session.connect()
            state = await redis_session._conn.get(redis_session.state)
            if state is not None and int(state) == 2:  # HOT
                stale.append((channel_class.__name__, anchor_id, 'HOT'))

    return stale


async def _scan_anchor_ids(channel_class):
    """Scan Redis for all anchor IDs that have state keys for a channel.

    Looks for keys matching the pattern ``{channel.name}:*:state`` and
    extracts the anchor_id portion.

    Args:
        channel_class: The ContextChannel subclass to scan for.

    Returns:
        list of anchor_id strings.
    """
    conn = redis_lib.asyncio.from_url(settings.REDIS_URL)
    pattern = f'{channel_class.name}:*:state'
    anchor_ids = []
    async for key in conn.scan_iter(match=pattern):
        # Key format: {channel.name}:{anchor_id}:state
        key_str = key.decode() if isinstance(key, bytes) else key
        parts = key_str.rsplit(':', 2)
        if len(parts) == 3:
            anchor_ids.append(parts[1])
    await conn.aclose()
    return anchor_ids
