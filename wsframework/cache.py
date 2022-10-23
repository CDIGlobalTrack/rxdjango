import json
import gzip
import pylibmc
from django.core.cache import cache
from django.utils import timezone


def get_cached(cache_key):
    cached_data = cache.get(cache_key)

    if not cached_data:
        return
    if cached_data.get('c'):
        data = gzip.decompress(cached_data['data']).decode()
        cached_data['data'] = json.loads(data)

    if cached_data.get('last_modified'):
        tstamp = cached_data['last_modified']

    return cached_data


def set_cached(cache_key, data):
    if not cache_key:
        return

    tstamp = timezone.now()

    packet = {
        'data': data,
        'last_modified': tstamp,
        'c': False,  # compressed
    }

    try:
        cache.set(cache_key, packet)
    except pylibmc.TooBig:
        serialized = json.dumps(data, default=str)
        packet['data'] = gzip.compress(serialized.encode())
        packet['c'] = True
        cache.set(cache_key, packet)

    return tstamp
