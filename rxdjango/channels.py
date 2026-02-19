from __future__ import annotations

import json
from typing import Any, ClassVar

import channels.layers
from channels.db import database_sync_to_async
from django.db import models, ProgrammingError
from django.db.models import Model, QuerySet
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
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
        meta.cache_ttl = getattr(meta, 'cache_ttl', None)

        if not isinstance(anchor, serializers.ModelSerializer):
            raise ProgrammingError(
                f'{new_class.__name__}.Meta.state must be an instance of '
                'serializers.ModelSerializer'
            )

        if getattr(meta, 'optimize_anchors', False):
            # Add _rx boolean field to model to filter active_channels
            module = new_class.__module__.replace('.', '_').lower()
            active_flag = f'_rx_{module}_{new_class.__name__.lower()}'
            model = anchor.Meta.model
            anchor.Meta.model.add_to_class(
                active_flag,
                models.BooleanField(null=True, default=False, db_index=True),
            )
        else:
            active_flag = None

        # Attach the state model, websocket router, and signal handler.
        new_class.meta = meta
        new_class.many = many
        new_class._state_model = StateModel(anchor, active_flag)
        new_class._wsrouter = WebsocketRouter(new_class.name)
        new_class._signal_handler = SignalHandler(new_class)
        new_class._anchor_model = anchor.Meta.model
        new_class._anchor_events_channel = f'{new_class.__name__}-anchor-events'
        new_class._consumer_methods = get_consumer_methods(new_class)

        # Register this channel class for cache expiry scanning
        ContextChannel._registry.add(new_class)

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
    _registry: ClassVar[set[type]] = set()

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

    def __init__(self, user: AbstractBaseUser, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.user = user
        self.user_id = user.id
        self._consumer = None  # will be set by consumer
        self.runtime_state = self.RuntimeState() if self.RuntimeState else None
        self.anchor_ids: list[int] = []
        self.anchor_index: set[int] = set()

    async def send(self, *args: Any, **kwargs: Any) -> None:
        """A proxy method to self._consumer.send"""
        await self._consumer.send(*args, **kwargs)

    async def group_add(self, group: str) -> None:
        """Add this consumer to a group in channels"""
        channel_layer = channels.layers.get_channel_layer()
        await channel_layer.group_add(
            group,
            self._consumer.channel_name,
        )

    async def initialize_anchors(self) -> None:
        if self.many:
            qs = await self.list_instances(**self.kwargs)
            self.anchor_ids = await self._fetch_instance_ids(qs)
        else:
            self.anchor_ids = [
                self.get_instance_id(**self.kwargs)
            ]
        self.anchor_index = set(self.anchor_ids)

    @database_sync_to_async
    def _fetch_instance_ids(self, qs: QuerySet) -> list[int]:
        return [
            instance['id'] for instance in qs.values('id')
        ]

    def get_instance_id(self, **kwargs: Any) -> int | str:
        """Subclass may implement get_anchor_id, based on url parameters,
        otherwise the first parameter will be assumed to be it"""
        return next(iter(kwargs.values()))

    async def list_instances(self, **kwargs: Any) -> QuerySet:
        """Subclass must implement this if serializer has many=True parameter.
        Returns a queryset"""
        raise NotImplemented

    async def add_instance(self, instance_id: int, at_beginning: bool = False) -> None:
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

    async def set_runtime_var(self, var: str, value: Any) -> None:
        self.runtime_state[var] = value
        payload = { 'runtimeVar': var, 'value': value }
        payload = json.dumps(payload)
        await self.send(text_data=payload)

    @database_sync_to_async
    def serialize_instance(self, instance: Model, tstamp: float = 0) -> dict[str, Any]:
        return self._state_model.serialize_instance(instance, tstamp)

    @database_sync_to_async
    def _check_instance(self, qs: QuerySet, instance: Model) -> bool:
        instance = qs.filter(id=instance.id).first()
        return bool(instance)

    async def remove_instance(self, instance_id: int) -> None:
        return await self._remove_instance(instance_id, True)

    async def _remove_instance(self, instance_id: int, remove: bool) -> None:
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

    async def clear(self) -> None:
        for instance_id in self.anchor_ids:
            await self._remove_instance(instance_id, False)
        self.anchor_ids = []
        self.anchor_index = set()

    @staticmethod
    def has_permission(user: AbstractBaseUser, **kwargs: Any) -> bool:
        """Implement this method to check if user has permission on a channel"""
        return NotImplemented

    async def is_visible(self, instance_id: int) -> bool:
        """Implement this to check if a new instance should be added to this
        channel. You should check if user permission on instance"""
        return NotImplemented

    async def on_connect(self, tstamp: float | None) -> None:
        """Called after user has been authenticated.
        tstamp is the tstamp sent on connection, if this is a reconnection"""
        pass

    async def on_disconnect(self) -> None:
        """Called when user disconnects"""
        pass

    RX_CACHE_TTL_DEFAULT = 7 * 24 * 3600  # 1 week

    @classmethod
    def get_cache_ttl(cls) -> int:
        """Get the cache TTL in seconds for this channel.

        Checks the channel's Meta.cache_ttl first, then falls back to
        the global RX_CACHE_TTL Django setting, then to 1 week default.

        Returns:
            TTL in seconds.
        """
        if cls.meta.cache_ttl is not None:
            return cls.meta.cache_ttl
        return getattr(settings, 'RX_CACHE_TTL', cls.RX_CACHE_TTL_DEFAULT)

    @classmethod
    def get_registered_channels(cls) -> set[type]:
        """Return all registered ContextChannel subclasses."""
        return cls._registry

    @classmethod
    async def clear_cache(cls, anchor_id: int) -> bool:
        """Clear cache for an anchor via the COOLING state.

        Transitions HOT → COOLING, migrates instances from MongoDB to
        Redis (for any clients that connect during the process), then
        transitions to COLD. If a client connects during COOLING, reheats
        by writing instances back to MongoDB.

        Returns:
            True if the cache was cleared (or reheated), False if not HOT.
        """
        redis_session = RedisStateSession(cls, anchor_id)
        if not await redis_session.start_cooling():
            return False
        return await cls._cooling_cycle(anchor_id, redis_session)

    @classmethod
    async def _cooling_cycle(cls, anchor_id: int, redis_session: RedisStateSession) -> bool:
        """Run the COOLING cycle: migrate MongoDB → Redis, then finish.

        Expects state to already be COOLING. Reads instances from MongoDB,
        pushes them to the Redis list, deletes from MongoDB, then atomically
        finishes. If a client connected during COOLING (state became HEATING),
        reheats by writing instances back to MongoDB.
        """
        import logging
        logger = logging.getLogger('rxdjango.cache_expiry')

        # Migrate instances from MongoDB to Redis list
        all_instances = []
        async for batch in MongoStateSession.list_and_clear_instances(
            cls, anchor_id
        ):
            all_instances.extend(batch)
            await redis_session.write_instances(batch)

        result = await redis_session.finish_cooling()

        if result == 1:
            # A client connected during COOLING (state is now HEATING).
            # Reheat: write instances back to MongoDB so end_cold_session
            # can transition to HOT.
            logger.info(
                'Reheating %s anchor %s (client connected during COOLING)',
                cls.__name__, anchor_id,
            )
            mongo = cls._create_mongo_writer(anchor_id)
            await mongo.write_instances(all_instances)
            await redis_session.end_cold_session(success=True)
        elif result == 0:
            logger.info('Expired %s anchor %s -> COLD', cls.__name__, anchor_id)
        else:
            logger.warning(
                'Unexpected state for %s anchor %s during finish_cooling (result=%s)',
                cls.__name__, anchor_id, result,
            )

        return True

    @classmethod
    def _create_mongo_writer(cls, anchor_id: int) -> MongoStateSession:
        """Create a MongoStateSession without requiring a full channel instance."""
        class _ChannelProxy:
            def __init__(self, channel_class):
                self.__class__.__name__ = channel_class.__name__
                self.user_id = None
                self._state_model = channel_class._state_model
        return MongoStateSession(_ChannelProxy(cls), anchor_id)

    @classmethod
    def broadcast_instance(cls, anchor_id: int, instance: Model, operation: str = 'update') -> None:
        cls._signal_handler.broadcast_instance(anchor_id, instance, operation)

    @classmethod
    def broadcast_notification(cls, anchor_id: int, notification: dict[str, Any], user_id: int | None = None) -> None:
        notification['_instance_type'] = '_notification'
        cls._wsrouter.sync_dispatch([notification], anchor_id, user_id)
