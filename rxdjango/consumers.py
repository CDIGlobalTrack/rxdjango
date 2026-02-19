"""WebSocket consumer for real-time state synchronization.

This module handles the full lifecycle of a WebSocket connection between
the Django backend and React frontend:

1. **Connection**: Accept WebSocket and wait for authentication
2. **Authentication**: Validate token via ``rest_framework.authtoken``
3. **State Loading**: Load initial state from MongoDB cache via StateLoader
4. **Real-time Sync**: Subscribe to update groups and relay changes
5. **Actions (RPC)**: Execute ``@action`` decorated methods from frontend
6. **Pub/Sub**: Handle ``@consumer`` decorated methods for group events

Message Protocol
----------------

Incoming messages (client -> server)::

    # Authentication (must be first message)
    {"token": "<rest_framework_auth_token>", "last_update": <timestamp|null>}

    # Action call (RPC)
    {"callId": <unique_id>, "action": "methodName", "params": [...]}

Outgoing messages (server -> client)::

    # Connection status
    {"status_code": 200}
    {"status_code": 401, "error": "error/unauthorized"}

    # Initial anchor list
    {"initialAnchors": [1, 2, 3]}

    # State instances (array of flat instances)
    [{"id": 1, "_instance_type": "app.Serializer", "_tstamp": ..., ...}, ...]

    # End of initial state marker
    [{"_instance_type": "", "_tstamp": ..., "_operation": "end_initial_state", "id": 0}]

    # Action response
    {"callId": <id>, "result": ...}
    {"callId": <id>, "error": "Error"}

    # Runtime state change
    {"runtimeVar": "varName", "value": ...}
"""
from __future__ import annotations

import json
from typing import Any, Callable
from datetime import datetime
from pytz import utc
from django.utils import timezone
from django.db import models
from django.db.models.query import QuerySet
from django.contrib.auth.models import AbstractBaseUser
from asgiref.sync import sync_to_async, async_to_sync
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from rest_framework.authtoken.models import Token
from .state_loader import StateLoader
from .actions import execute_action
from .exceptions import UnauthorizedError, ForbiddenError, AnchorDoesNotExist
from .redis import RedisSession
from rxdjango.serialize import json_dumps


class StateConsumer(AsyncWebsocketConsumer):
    """WebSocket consumer that manages real-time state synchronization.

    This consumer handles the full lifecycle of a WebSocket connection:

    1. Connection acceptance (no auth required yet)
    2. Token-based authentication via Django REST Framework
    3. Initial state loading from MongoDB cache
    4. Real-time update subscription via channel groups
    5. Action (RPC) execution from frontend calls
    6. Graceful disconnection with group cleanup

    A subclass of StateConsumer is dynamically created by
    ``ContextChannel.as_asgi()`` with ``context_channel_class`` and
    ``wsrouter`` attributes set.

    Attributes:
        channel: The ContextChannel instance managing this connection.
        user: The authenticated Django user, or None before authentication.
        token: The authentication token string.
        anchor_ids: List of anchor instance IDs this consumer is subscribed to.
        wsrouter: The WebsocketRouter for dispatching updates.
        tstamp: Timestamp of the last loaded state, used for incremental updates.
    """
    def __init__(self, *args: Any, **kwargs: Any) -> None:

        super().__init__(*args, **kwargs)

        self.channel = None
        self.user: AbstractBaseUser | None = None
        self.token: str | None = None
        self.anchor_ids: list[int] | None = None
        self.wsrouter = None
        self.tstamp: float | None = None
        self.session = None

    async def connect(self) -> None:
        """Accept any connection and just wait for a token."""
        await self.accept()

    async def receive(self, text_data: str) -> None:
        """Handle incoming WebSocket message.

        Routes messages based on authentication state:
        - Before auth: treated as authentication message
        - After auth: treated as action (RPC) call
        """
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.disconnect()
            raise

        if self.user:
            await self.receive_action(data)
        else:
            await self.receive_authentication(data)

    async def receive_authentication(self, text_data: dict[str, Any]) -> None:
        """Handle authentication message from client.

        Expected message format::

            {"token": "<rest_framework_auth_token>", "last_update": <timestamp|null>}

        On success: authenticates user, checks permissions, loads initial state.
        On failure: sends error status and closes connection.

        Args:
            text_data: Parsed JSON dict with 'token' and optional 'last_update' keys.
        """
        # If user is not logged, we expect credentials
        # data = json.loads(text_data)
        data = text_data
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

    async def start(self, user: AbstractBaseUser, tstamp: float | None) -> None:
        """Initialize the channel after successful authentication.

        Sets up the ContextChannel instance, registers @consumer methods,
        subscribes to anchor groups, and loads initial state for each anchor.

        Args:
            user: The authenticated Django user.
            tstamp: Last known timestamp for incremental state loading on reconnect.
        """
        kwargs = self.scope['url_route']['kwargs']
        self.user = user
        self.channel = self.context_channel_class(user, **kwargs)
        self.channel._consumer = self
        await self.send_connection_status(200)
        await self.channel.initialize_anchors()
        for method_name in self.channel._consumer_methods.keys():
            event_type, func = self.channel._consumer_methods[method_name]
            local_method_name = event_type.replace('.', '_')
            if getattr(self, local_method_name, None):
                raise TypeError(f"Can't override method {local_method_name} in consumer,"
                                " chose another type for this event")
            # Inject a method in consumer matching the type name with
            # the decorated method in channel
            async def method(event):
                return await func(self.channel, event)
            setattr(self, local_method_name, method)
        self.user_id = self.user.id
        self.anchor_ids = self.channel.anchor_ids
        self.wsrouter = self.channel._wsrouter

        for anchor_id in self.anchor_ids:
            await self.connect_anchor(anchor_id)

        if self.channel.meta.auto_update:
            await self.channel_layer.group_add(
                self.channel._anchor_events_channel,
                self.channel_name,
            )

        await self.channel.on_connect(tstamp)

        await self.send(text_data=json.dumps({
            'initialAnchors': self.anchor_ids,
        }))

        for anchor_id in self.anchor_ids:
            await self._load_state(anchor_id)

    async def instances_list_add(self, event: dict[str, Any]) -> None:
        """Handle channel layer event for adding an instance to a many=True list.

        Called when a new instance is created and auto_update is enabled.
        Checks visibility via ``channel.is_visible()`` before adding.
        """
        instance_id = event['instance_id']
        if await self.channel.is_visible(instance_id):
            await self.channel.add_instance(instance_id, at_beginning=True)

    async def instances_list_remove(self, event: dict[str, Any]) -> None:
        """Handle channel layer event for removing an instance from a many=True list."""
        instance_id = event['instance_id']
        await self.channel.remove_instance(instance_id)

    async def connect_anchor(self, anchor_id: int) -> None:
        """Subscribe this consumer to WebSocket updates for the given anchor."""
        await self.wsrouter.connect(
            self.channel_layer,
            self.channel_name,
            anchor_id,
            self.user_id,
        )
        redis = RedisSession(self.context_channel_class, anchor_id)
        await redis.session_connect()

    async def disconnect_anchor(self, anchor_id: int) -> None:
        """Unsubscribe this consumer from WebSocket updates for the given anchor."""
        await self.wsrouter.disconnect(
            self.channel_layer,
            self.channel_name,
            anchor_id,
            self.user_id,
        )
        redis = RedisSession(self.context_channel_class, anchor_id)
        await redis.session_disconnect()

    async def _load_state(self, anchor_id: int) -> None:
        """Load and send initial state for an anchor from MongoDB cache.

        Streams state instances to the client in batches, then sends
        an end-of-data marker with the current timestamp.
        """
        async with StateLoader(self.channel, anchor_id) as loader:
            async for instances in loader.list_instances():
                if instances:
                    data = json_dumps(instances)
                    await self.send(text_data=data)

            self.tstamp = loader.tstamp
            await self.send(text_data=self.end_of_data)

    @property
    def end_of_data(self) -> str:
        """JSON string marking the end of initial state loading."""
        return json_dumps([{
            '_instance_type': '',
            '_tstamp': self.tstamp,
            '_operation': 'end_initial_state',
            'id': 0,
        }])

    @database_sync_to_async
    def serialized_data(self, Serializer, data):
        return Serializer(data).data

    async def disconnect(self, close_code: int | None = None) -> None:
        """Handle WebSocket disconnection.

        Unsubscribes from all anchor groups and calls ``channel.on_disconnect()``.
        """
        if not self.channel:
            return

        for anchor_id in self.anchor_ids:
            await self.wsrouter.disconnect(
                self.channel_layer,
                self.channel_name,
                anchor_id,
                self.user_id,
            )
            redis = RedisSession(self.context_channel_class, anchor_id)
            await redis.session_disconnect()

        await self.channel.on_disconnect()

    @database_sync_to_async
    def authenticate(self, token: str) -> AbstractBaseUser:
        """Validate an authentication token and check channel permissions.

        Looks up the token via ``rest_framework.authtoken.models.Token``,
        then calls ``context_channel_class.has_permission()`` to verify access.

        Args:
            token: The DRF auth token string from the client.

        Returns:
            The authenticated Django user.

        Raises:
            UnauthorizedError: If the token is invalid or missing.
            ForbiddenError: If ``has_permission()`` returns False.
        """
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

    async def relay(self, payload: dict[str, Any]) -> None:
        """Relay a state update payload to the connected client.

        Called by the channel layer when the WebsocketRouter dispatches updates.
        """
        payload = payload['payload']
        await self.send(text_data=json_dumps(payload))

    async def receive_action(self, action: dict[str, Any]) -> None:
        """Execute an @action decorated method via RPC from the frontend.

        Expected message format::

            {"callId": <unique_id>, "action": "methodName", "params": [...]}

        Sends back the result or error with the same callId.

        Args:
            action: Parsed JSON dict with 'callId', 'action', and 'params' keys.
        """
        call_id = action['callId']
        method_name = action.pop('action')
        params = action.pop('params')

        try:
            action['result'] = await execute_action(self.channel, method_name, params)
            await self.send(text_data=json.dumps(action))
        except Exception as e:
            action['error'] = 'Error'
            await self.send(text_data=json.dumps(action))
            raise

    async def send_connection_status(self, status_code: int, error: str | None = None) -> None:
        """Send a connection status message to the client.

        Args:
            status_code: HTTP-like status code (200, 401, 403, 404).
            error: Optional error string. If provided, the connection is closed.
        """
        data = {}
        data['status_code'] = status_code
        if error:
            data['error'] = error
        text_data = json_dumps(data)
        close = error != None
        await self.send(text_data=text_data, close=close)

    async def prepend_anchor_id(self, anchor_id: int) -> None:
        """Notify the client to prepend an anchor ID to its list.

        Used for many=True channels when a new instance is added at the beginning.
        """
        data = {
            'prependAnchor': anchor_id,
        }
        text_data = json_dumps(data)
        await self.send(text_data=text_data)


__CONSUMERS = dict()


def consumer(event_type: str) -> Callable[[Callable], Callable]:
    """Decorator to subscribe a ContextChannel method to Django Channels events.

    The decorated method will be called when an event of the specified
    type is received on any group the consumer is subscribed to.
    You must manually call ``group_add(group)`` in ``on_connect()`` to
    subscribe to the group.

    Args:
        event_type: The event type string to listen for (e.g. 'chat.message').

    Example::

        class MyChannel(ContextChannel):
            async def on_connect(self, tstamp):
                await self.group_add('my_group')

            @consumer('chat.message')
            async def handle_chat(self, event):
                await self.send(text_data=json.dumps(event['data']))
    """
    if not isinstance(event_type, str):
        raise TypeError("Parameter group @consumer decorator must be a string")

    def decorator(func: Callable) -> Callable:
        if not callable(func):
            raise TypeError("@consumer(group) is a decorator and needs to decorate"
                            " a method in a ContextChannel class")
        key = '.'.join((func.__module__, func.__qualname__))
        __CONSUMERS[key] = (event_type, func)

        return func

    return decorator


def get_consumer_methods(cls: type) -> dict[str, tuple[str, Callable]]:
    """Collect and return all @consumer decorated methods for a channel class.

    Pops matching entries from the global __CONSUMERS registry to avoid
    duplicate registrations.
    """
    consumers = __CONSUMERS
    keys = list(__CONSUMERS.keys())
    qualname = '.'.join((cls.__module__, cls.__qualname__))
    consumer_methods: dict[str, tuple[str, Callable]] = {}
    for key in keys:
        if key.startswith(qualname):
            method_name = key[len(qualname) + 1:]
            event_type, func = __CONSUMERS.pop(key)
            consumer_methods[method_name] = (event_type, func)
    return consumer_methods
