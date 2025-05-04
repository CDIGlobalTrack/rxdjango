import json
import channels.layers
from channels.db import database_sync_to_async
from django.db import ProgrammingError
from rest_framework import serializers

from .consumers import StateConsumer, get_consumer_methods
from .state_model import StateModel
from .state_loader import StateLoader
from .websocket_router import WebsocketRouter
from .signal_handler import SignalHandler
from .redis import RedisStateSession
from .mongo import MongoStateSession
from .serialize import json_dumps

class ContextChannelMeta(type):
    """Metaclass for the ContextChannel.

    Builds the state model based on the provided fields and Meta class,
    and assigns a WebsocketRouter and a SignalHandler to it.
    """

    def __new__(cls, name, bases, attrs):
        """Create and return a new class instance."""

        # Create the new class as usual.
        new_class = super().__new__(cls, name, bases, attrs)

        if new_class.__module__ == cls.__module__:
            return new_class

        # Set the name to be used for mongo collection names and redis keys.
        new_class.name = '_'.join([
            new_class.__module__.replace('.channels', '').replace('.', '_'),
            new_class.__name__.lower()
        ])

        # Ensure the Meta class and anchor serializer are provided.
        meta = attrs.get("Meta")
        if not meta:
            raise ProgrammingError(
                f'{new_class.__name__} must define a Meta class'
            )

        meta.abstract = getattr(meta, 'abstract', False)
        if meta.abstract:
            return new_class

        # The "state" property defines the top-level serializer that will
        # be used to build the context state.
        # This top-level serializer is called "anchor" all over the code,
        # while anchor_id refers to the instance used to build the state.
        if not hasattr(meta, "state"):
            raise ProgrammingError(
                f'{new_class.__name__} must define a Meta class setting the'
                '"state" property to a serializer inheriting from'
                'serializers.ModelSerializer'
            )

        anchor = meta.state
        many = False
        if isinstance(anchor, serializers.ListSerializer):
            many = True
            anchor = anchor.child

        meta.auto_update = getattr(meta, 'auto_update', False) and many

        if not isinstance(anchor, serializers.ModelSerializer):
            raise ProgrammingError(
                f'{new_class.__name__}.Meta.state must be an instance of '
                'serializers.ModelSerializer'
            )

        # Attach the state model, websocket router, and signal handler.
        new_class.meta = meta
        new_class.many = many
        new_class._state_model = StateModel(anchor)
        new_class._wsrouter = WebsocketRouter(new_class.name)
        new_class._signal_handler = SignalHandler(new_class)
        new_class._anchor_model = anchor.Meta.model
        new_class._anchor_events_channel = f'{new_class.__name__}-anchor-events'
        new_class._consumer_methods = get_consumer_methods(new_class)
        return new_class


class ContextChannel(metaclass=ContextChannelMeta):
    """This is the core API of RxDjango.
    ContextChannel provides a state that is synchronized with
    all connected clients through a websocket.
    Frontend classes are automatically generated for each
    ContextChannel subclass.
    Subclasses of ContextChannel should reside in a file named
    channels.py inside a django app.
    """

    class Meta:
        abstract = True

    RuntimeState = None

    @classmethod
    def as_asgi(cls):
        Consumer = type(
            f'{cls.__name__}Consumer',
            (StateConsumer,),
            dict(
                context_channel_class=cls,
                wsrouter=cls._wsrouter,
            ),
        )

        return Consumer.as_asgi()

    def __init__(self, user, **kwargs):
        self.kwargs = kwargs
        self.user = user
        self.user_id = user.id
        self._consumer = None  # will be set by consumer
        self.runtime_state = self.RuntimeState() if self.RuntimeState else None
        self.anchor_ids = []
        self.anchor_index = set()

    async def send(self, *args, **kwargs):
        """A proxy method to self._consumer.send"""
        await self._consumer.send(*args, **kwargs)

    async def group_add(self, group):
        """Add this consumer to a group in channels"""
        channel_layer = channels.layers.get_channel_layer()
        await channel_layer.group_add(
            group,
            self._consumer.channel_name,
        )

    async def initialize_anchors(self):
        if self.many:
            qs = await self.list_instances(**self.kwargs)
            if isinstance(qs, models.query.QuerySet):
                self.anchor_ids = await self._fetch_instance_ids(qs)
            elif len(qs) == 0:
                self.anchor_ids = []
            else:
                assert type(qs[0]) is int
                self.anchor_ids = qs
        else:
            self.anchor_ids = [
                self.get_instance_id(**self.kwargs)
            ]
        self.anchor_index = set(self.anchor_ids)

    @database_sync_to_async
    def _fetch_instance_ids(self, qs):
        return [
            instance['id'] for instance in qs.values('id')
        ]

    def get_instance_id(self, **kwargs):
        """Subclass may implement get_anchor_id, based on url parameters,
        otherwise the first parameter will be assumed to be it"""
        return next(iter(kwargs.values()))

    async def list_instances(self, **kwargs):
        """Subclass must implement this if serializer has many=True parameter.
        Returns a queryset"""
        raise NotImplemented

    async def add_instance(self, instance_id, at_beginning=False):
        if instance_id in self.anchor_index:
            return
        if at_beginning:
            await self._consumer.prepend_anchor_id(instance_id)
        self.anchor_ids.append(instance_id)
        self.anchor_index.add(instance_id)
        await self._consumer.connect_anchor(instance_id)
        async with StateLoader(self, instance_id) as loader:
            async for instances in loader.list_instances():
                if instances:
                    for instance in instances:
                        instance['_operation'] = 'initial_state'
                    data = json_dumps(instances)
                    await self.send(text_data=data)

    async def set_runtime_var(self, var, value):
        self.runtime_state[var] = value
        payload = { 'runtimeVar': var, 'value': value }
        payload = json.dumps(payload)
        await self.send(text_data=payload)

    @database_sync_to_async
    def serialize_instance(self, instance, tstamp=0):
        return self._state_model.serialize_instance(instance, tstamp)

    @database_sync_to_async
    def _check_instance(self, qs, instance):
        instance = qs.filter(id=instance.id).first()
        return bool(instance)

    async def remove_instance(self, instance_id):
        return await self._remove_instance(instance_id, True)

    async def _remove_instance(self, instance_id, remove):
        if instance_id not in self.anchor_ids:
            return
        serialized = {
            'id': instance_id,
            '_operation': 'delete',
            '_instance_type': self._state_model.instance_type,
        }
        await self._consumer.disconnect_anchor(instance_id)
        await self.send(text_data=json.dumps([serialized], default=str))
        if remove:
            self.anchor_ids.remove(instance_id)
            self.anchor_index.remove(instance_id)

    async def clear(self):
        for instance_id in self.anchor_ids:
            await self._remove_instance(instance_id, False)
        self.anchor_ids = []
        self.anchor_index = set()

    @staticmethod
    def has_permission(user, **kwargs):
        """Implement this method to check if user has permission on a channel"""
        return NotImplemented

    async def is_visible(self, instance_id):
        """Implement this to check if a new instance should be added to this
        channel. You should check if user permission on instance"""
        return NotImplemented

    async def on_connect(self, tstamp):
        """Called after user has been authenticated.
        tstamp is the tstamp sent on connection, if this is a reconnection"""
        pass

    async def on_disconnect(self):
        """Called when user disconnects"""
        pass

    @classmethod
    async def clear_cache(cls, anchor_id):
        redis = RedisStateSession(cls, anchor_id)
        result = await redis.cooldown()
        if not result:
            return result
        await MongoStateSession.clear(cls, anchor_id)
        return result

    @classmethod
    def broadcast_instance(cls, anchor_id, instance, operation='update'):
        cls._signal_handler.broadcast_instance(anchor_id, instance, operation)

    @classmethod
    def broadcast_notification(cls, anchor_id, notification, user_id=None):
        notification['_instance_type'] = '_notification'
        cls._wsrouter.sync_dispatch([notification], anchor_id, user_id)
