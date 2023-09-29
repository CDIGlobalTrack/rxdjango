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
from .actions import execute_action
from .exceptions import UnauthorizedError, ForbiddenError, AnchorDoesNotExist
from rxdjango.serialize import json_dumps


class StateConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer that manages user authentication, session management,
    and real-time data relay to clients. A subclass of StateConsumer will be
    dinamically created by ContextChannel.as_asgi().
    """
    def __init__(self, *args, **kwargs):

        super().__init__(*args, **kwargs)

        self.channel = None
        self.user = None
        self.token = None
        self.anchor_ids = None
        self.wsrouter = None
        self.tstamp = None
        self.session = None

    async def connect(self):
        """Accept any connection and just wait for a token."""
        await self.accept()

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.disconnect()
            raise

        if self.user:
            await self.receive_action(data)
        else:
            await self.receive_authentication(data)

    async def receive_authentication(self, text_data):
        # If user is not logged, we expect credentials
        data = json.loads(text_data)
        token = data.get('token', None)
        last_update  = data.get('last_update', None)

        user = None
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
        self.channel = self.context_channel_class(user, **kwargs)
        await self.channel.initialize_anchors()
        self.channel._consumer = self
        self.user_id = self.user.id
        self.anchor_ids = self.channel.anchor_ids
        self.wsrouter = self.channel._wsrouter

        await self.send_connection_status(200)

        if self.channel.many:
            await self.send(text_data=json.dumps(self.anchor_ids))

        for anchor_id in self.anchor_ids:
            await self.connect_anchor(anchor_id)

        await self.channel.on_connect(tstamp)

        for anchor_id in self.anchor_ids:
            await self._load_state(anchor_id)

    async def connect_anchor(self, anchor_id):
        await self.wsrouter.connect(
            self.channel_layer,
            self.channel_name,
            anchor_id,
            self.user_id,
        )

    async def disconnect_anchor(self, anchor_id):
        await self.wsrouter.disconnect(
            self.channel_layer,
            self.channel_name,
            anchor_id,
            self.user_id,
        )

    async def _load_state(self, anchor_id):
        async with StateLoader(self.channel, anchor_id) as loader:
            async for instances in loader.list_instances():
                if instances:
                    data = json_dumps(instances)
                    await self.send(text_data=data)

            self.tstamp = loader.tstamp
            await self.send(text_data=self.end_of_data)

    @property
    def end_of_data(self):
        return json_dumps([{
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

        for anchor_id in self.anchor_ids:
            await self.wsrouter.disconnect(
                self.channel_layer,
                self.channel_name,
                anchor_id,
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
        if not self.context_channel_class.has_permission(
                token.user,
                **kwargs,
        ):
            raise ForbiddenError('error/forbidden')
            return

        return token.user

    async def relay(self, payload):
        payload = payload['payload']
        await self.send(text_data=json_dumps(payload))

    async def receive_action(self, action):
        call_id = action['callId']
        method_name = action.pop('action')
        params = action.pop('params')

        try:
            action['result'] = execute_action(self.channel, method_name, params)
            await self.send(text_data=json.dumps(action))
        except Exception as e:
            action['error'] = 'Error'
            await self.send(text_data=json.dumps(action))
            raise

    async def send_connection_status(self, status_code, error=None):
        data = {}
        data['status_code'] = status_code
        if error:
            data['error'] = error
        text_data = json_dumps(data)
        close = error != None
        await self.send(text_data=text_data, close=close)
