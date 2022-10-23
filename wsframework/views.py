import json
import redis
from datetime import datetime
from django.conf import settings
from django.views import View
from django.http import HttpResponse
from rest_framework.response import Response

def connect():
    return redis.Redis(host=settings.REDIS_HOST,
                       port=settings.REDIS_PORT,
                       db=settings.REDIS_DB)


def create_snapshot_link(token, key, snapshot, last_modified):
    redis_key = f'{token}-{key}'
    conn = connect()
    data = dict(data=snapshot, last_modified=last_modified)

    conn.setex(redis_key,
               settings.SNAPSHOT_CACHE_TIMEOUT,
               json.dumps(data, default=str))
    return f'/api/wsframework/{key}/'



class CachedSnapshotView(View):

    def get(self, request, key):
        conn = connect()
        try:
            token = request.headers['Authorization'].split()[1]
        except (KeyError, IndexError):
            return HttpResponse(status=403)
        redis_key = f'{token}-{key}'

        cached = conn.get(redis_key)
        if not cached:
            return HttpResponse(status=404)

        # Attention: If two sessions in same run and same token are open at same time,
        # one of them might break.
        conn.delete(redis_key)

        cached = json.loads(cached)

        browser_cache = request.headers.get('If-Modified-Since')

        if browser_cache:
            tstamp = datetime.fromisoformat(browser_cache)
            last_modified = datetime.fromisoformat(cached['last_modified'])
            if last_modified == tstamp:
                return HttpResponse(status=304)

        headers = {
            'Cache-Control': 'max-age=0',
            'Last-Modified': cached['last_modified'],
            'Content-type': 'application/json',
        }

        return HttpResponse(json.dumps(cached['data']),
                            status=200,
                            headers=headers)
