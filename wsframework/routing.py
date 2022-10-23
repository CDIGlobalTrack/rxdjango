from django.urls import re_path
from django.conf import settings

from wsframework import consumers


try:
    prefix = settings.WSFRAMEWORK_URL_PREFIX
except AttributeError:
    prefix = ''

websocket_urlpatterns = [
    re_path(f'{prefix}(?P<channel_type>[a-z_]+)/(?P<channel_instance_id>.+)/$',
            consumers.TransactionConsumer.as_asgi()),
]
