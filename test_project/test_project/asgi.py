"""
ASGI config for test_project project.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'test_project.settings')

app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter
from test_project.urls import websocket_urlpatterns

application = ProtocolTypeRouter({
    "http": app,
    "websocket": URLRouter(websocket_urlpatterns),
})
