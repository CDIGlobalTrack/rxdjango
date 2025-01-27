import json
from collections import deque, namedtuple
import pymongo
from django.utils import timezone
from hashlib import md5
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from django.conf import settings
from django.db import models, connection, transaction, ProgrammingError
from rest_framework import serializers

from .consumers import StateConsumer
from .state_model import StateModel
from .websocket_router import WebsocketRouter
from .signal_handler import SignalHandler
from .redis import RedisStateSession
from .mongo import MongoStateSession


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

        if not isinstance(anchor, serializers.ModelSerializer):
            raise ProgrammingError(
                f'{new_class.__name__}.Meta.state must be an instance of '
                'serializers.ModelSerializer'
            )

        # Attach the state model, websocket router, and signal handler.
        new_class._state_model = StateModel(anchor)
        new_class._wsrouter = WebsocketRouter(new_class.name)
        new_class._signal_handler = SignalHandler(new_class)
        new_class.many = many

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
     
    async def initialize_anchors(self): 
        if self.many:
            qs = await self.list_instances(**self.kwargs)
            await self._fetch_instance_ids(qs)
        else:
            self.anchor_ids = [
                self.get_instance_id(**self.kwargs)
            ]
            
    @database_sync_to_async
    def _fetch_instance_ids(self, qs):
        self.anchor_ids = [
                instance['id'] for instance in qs.values('id')
            ]

    def get_instance_id(self, **kwargs):
        """Subclass may implement get_anchor_id, based on url parameters,
        otherwise the first parameter will be assumed to be it"""
        return next(iter(kwargs.values()))

    def list_instances(self, **kwargs):
        """Subclass must implement this if serializer has many=True parameter.
        Returns a queryset"""
        raise NotImplemented

    @staticmethod
    def has_permission(user, **kwargs):
        """Implement this method to check if user has permission on a channel"""
        return True

    async def on_connect(self, tstamp):
        """Called after user has been authenticated.
        tstamp is the tstamp sent on connection, if this is a reconnection"""
        pass

    async def on_disconnect(self):
        """Called when user disconnects"""
        pass

    async def receive(self, data):
        """Any data sent by user will get here"""
        pass

    async def clear_cache(self):
        redis = RedisStateSession(self)
        mongo = MongoStateSession(self)
        result = await redis.cooldown()
        if result:
            await mongo.clear()
        return result

    @classmethod
    def broadcast_instance(cls, anchor_id, instance, operation='update'):
        cls._signal_handler.broadcast_instance(anchor_id, instance, operation)

    @classmethod
    def broadcast_notification(cls, anchor_id, notification, user_id=None):
        notification['_instance_type'] = '_notification'
        cls._wsrouter.sync_dispatch([notification], anchor_id, user_id)
