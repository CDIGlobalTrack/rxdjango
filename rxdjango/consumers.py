import json
from datetime import datetime
from pytz import utc
from django.utils import timezone
from django.db import models
from django.db.models.query import QuerySet
from asgiref.sync import sync_to_async, async_to_sync
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from rest_framework.authtoken.models import Token
from .state_loader import StateLoader
from .exceptions import UnauthorizedError, ForbiddenError, AnchorDoesNotExist


class StateConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer that manages user authentication, session management,
    and real-time data relay to clients. A subclass of StateConsumer will be
    dinamically created by StateChannel.as_asgi().
    """
    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        self.channel = None
        self.user = None
        self.token = None
        self.anchor_id = None
        self.wsrouter = None
        self.tstamp = None
        self.session = None

    async def connect(self):
        """Accept any connection and just wait for a token."""
        await self.accept()

    async def receive(self, text_data):
        if self.user:
            await self.receive_command(text_data)
        else:
            await self.receive_authentication(text_data)

    async def receive_authentication(self, text_data):
        # If user is not logged, we expect credentials
        data = json.loads(text_data)
        token = data.get('token', None)
        last_update  = data.get('last_update', None)

        try:
            user = await self.authenticate(token=token)
        except UnauthorizedError:
            await self.send_connection_status(401, 'error/unauthorized')
        except ForbiddenError:
            await self.send_connection_status(403, 'error/forbidden')

        if user:
            try:
                await self.start(user, last_update)
            except AnchorDoesNotExist:
                await self.send_connection_status(404, 'error/not-found')

        else:
            await self.close()

    async def start(self, user, tstamp):
        kwargs = self.scope['url_route']['kwargs']
        self.user = user
        self.channel = self.state_channel_class(user, **kwargs)
        self.user_id = self.user.id
        self.anchor_id = self.channel.anchor_id
        self.wsrouter = self.channel._wsrouter

        await self.wsrouter.connect(
            self.channel_layer,
            self.channel_name,
            self.anchor_id,
            self.user_id,
        )

        async with StateLoader(self.channel) as loader:
            await self.send_connection_status(200)
            await self.channel.on_connect(tstamp)

            async for instances in loader.list_instances():
                if instances:
                    data = json.dumps(instances, default=str)
                    await self.send(text_data=data)

            self.tstamp = loader.tstamp
            await self.send(text_data=self.end_of_data)

    @property
    def end_of_data(self):
        return json.dumps([{
            '_instance_type': '',
            '_tstamp': self.tstamp,
            '_operation': 'end_initial_state',
            'id': 0,
        }])

    @database_sync_to_async
    def serialized_data(self, Serializer, data):
        return Serializer(data).data

    async def disconnect(self, close_code=None):
        if not self.channel:
            return

        await self.wsrouter.disconnect(
            self.channel_layer,
            self.channel_name,
            self.anchor_id,
            self.user_id,
        )

        await self.channel.on_disconnect()

    @database_sync_to_async
    def authenticate(self, token):
        try:
            token = Token.objects.get(key=token)
        except Token.DoesNotExist:
            raise UnauthorizedError('error/unauthorized')
            return
        self.token = token.key

        kwargs = self.scope['url_route']['kwargs']
        if not self.state_channel_class.has_permission(
                token.user,
                **kwargs,
        ):
            raise ForbiddenError('error/forbidden')
            return

        return token.user

    async def relay(self, payload):
        payload = payload['payload']
        await self.send(text_data=json.dumps(payload, default=str))

    async def receive_command(self, text_data):
        # If user is logged, we expect a JSON command
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.disconnect()
        await self.channel.receive(data)

    async def send_connection_status(self, status_code, error=None):
        data = {}
        data['status_code'] = status_code
        if error:
            data['error'] = error
        text_data = json.dumps(data)
        close = error != None
        await self.send(text_data=text_data, close=close)
