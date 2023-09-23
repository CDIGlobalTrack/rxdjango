import json
from collections import deque, namedtuple
import pymongo
from django.utils import timezone
from hashlib import md5
from asgiref.sync import async_to_sync
from django.conf import settings
from django.db import models, connection, transaction, ProgrammingError
from rest_framework import serializers
from backend.celery import app

from .consumers import StateConsumer
from .transaction import get_transaction_id
from .state_model import StateModel
from .websocket_router import WebsocketRouter
from .signal_handler import SignalHandler


class StateChannelMeta(type):
    """Metaclass for the StateChannel.

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

        if not hasattr(meta, "anchor"):
            raise ProgrammingError(
                f'{new_class.__name__} must define a Meta class with an '
                'anchor serializer'
            )

        anchor = meta.anchor

        if not isinstance(anchor, serializers.ModelSerializer):
            raise ProgrammingError(
                f'{new_class.__name__}.Meta.anchor must be an instance of '
                'serializers.ModelSerializer'
            )

        # Attach the state model, websocket router, and signal handler.
        new_class._state_model = StateModel(anchor)
        new_class._wsrouter = WebsocketRouter(new_class.name)
        new_class._signal_handler = SignalHandler(new_class)

        return new_class


class StateChannel(metaclass=StateChannelMeta):

    class Meta:
        abstract = True

    @classmethod
    def as_asgi(cls):
        Consumer = type(
            f'{cls.__name__}Consumer',
            (StateConsumer,),
            dict(
                state_channel_class=cls,
                wsrouter=cls._wsrouter,
            ),
        )

        return Consumer.as_asgi()

    def __init__(self, user, **kwargs):
        self.kwargs = kwargs
        self.user = user
        self.user_id = user.id
        self.anchor_id = self.get_anchor_id(**kwargs)

    def get_anchor_id(self, **kwargs):
        """Subclass may implement get_anchor_id, based on url parameters,
        otherwise the first parameter will be assumed to be it"""
        return next(iter(kwargs.values()))

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

    @classmethod
    def broadcast_instance(cls, anchor_id, instance, operation='update'):
        cls._signal_handler.broadcast_instance(anchor_id, instance, operation)

    @classmethod
    def broadcast_notification(cls, anchor_id, notification, user_id=None):
        notification['_instance_type'] = '_notification'
        cls._wsrouter.sync_dispatch([notification], anchor_id, user_id)
