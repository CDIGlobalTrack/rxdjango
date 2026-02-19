"""MongoDB cache layer for storing flattened instance state.

This module provides the persistent cache backing RxDjango's real-time state
synchronization. Serialized model instances are stored as flat documents in
MongoDB, keyed by anchor ID, instance type, and instance ID.

Two writer classes exist for different execution contexts:

- ``MongoStateSession``: Async writer used by WebSocket consumers during
  initial state loading and reconnection. Uses Motor (async MongoDB driver).
- ``MongoSignalWriter``: Sync writer used by Django signal handlers when
  model changes occur. Uses PyMongo (sync driver). Computes deltas between
  old and new state to minimize WebSocket payload size.

Documents that exceed MongoDB's 16MB size limit are transparently stored
in GridFS and referenced via a ``_grid_ref`` field.
"""

import json
from datetime import datetime
from copy import copy
from decimal import Decimal
import pymongo
import gridfs
from motor import motor_asyncio
from django.db import ProgrammingError
from django.conf import settings
from .redis import get_tstamp, sync_get_tstamp
from .serialize import json_dumps
try:
    from rxdjango.utils import delta_utils_c as delta_utils
except ImportError:
    from rxdjango.utils import delta_utils


class MongoStateSession:
    """Async MongoDB session for reading and writing cached instance state.

    Used by WebSocket consumers to load initial state when a client connects
    and to write state during reconnection catch-up. Each session is scoped
    to a single channel and anchor ID.

    Attributes:
        channel: The ContextChannel consumer instance.
        anchor_id: The root object ID that scopes the state tree.
        user_id: The authenticated user's ID, used for user-scoped filtering.
        state_model: The StateModel describing the nested serializer tree.
        db: The Motor async database handle.
        collection: The Motor async collection, named after the channel class.
    """

    def __init__(self, channel, anchor_id):
        """Initialize an async MongoDB session.

        Args:
            channel: The ContextChannel consumer instance. Provides user_id
                and the state model for serializer introspection.
            anchor_id: The root object ID that scopes all queries in this session.
        """
        self.channel = channel
        self.anchor_id = anchor_id
        self.user_id = channel.user_id
        self.state_model = channel._state_model
        self._tstamp = None

        client = motor_asyncio.AsyncIOMotorClient(settings.MONGO_URL)
        self.db = client[settings.MONGO_STATE_DB]
        self.collection = self.db[channel.__class__.__name__.lower()]

    async def tstamp(self):
        """Get the current Redis timestamp, caching it for the session lifetime.

        Returns:
            The timestamp string from Redis, used for ordering updates.
        """
        if self._tstamp:
            return self._tstamp
        self._tstamp = await get_tstamp()
        return self._tstamp

    async def list_instances(self, user_id):
        """Yield batches of cached instances for the initial state load.

        Iterates over each model layer in the state tree and queries MongoDB
        for all non-deleted instances matching the anchor and user. Instances
        stored in GridFS (due to exceeding the 16MB document limit) are
        transparently fetched and deserialized.

        Each yielded batch contains instances of a single ``_instance_type``,
        with ``_operation`` set to ``'initial_state'``.

        Args:
            user_id: The authenticated user's ID. Instances are filtered to
                those with ``_user_key`` of ``None`` (shared) or matching
                this user ID.

        Yields:
            list[dict]: Batches of instance dicts, one batch per model layer
                that has cached data.
        """
        for model in self.state_model.models():
            instances = []
            query = {
                '_anchor_id': self.anchor_id,
                '_user_key': {'$in': [None, user_id]},
                '_instance_type': model.instance_type,
                '_deleted': {'$ne': True},
            }

            async for instance in self.collection.find(query):
                try:
                    grid_ref = instance['_grid_ref']
                except KeyError:
                    pass
                else:
                    fs = gridfs.GridFS(self.db)
                    serialized = fs.get(grid_ref)
                    instance = json.loads(serialized.decode())

                del instance['_id']
                del instance['_anchor_id']
                instance['_operation'] = 'initial_state'
                instances.append(instance)

            if instances:
                yield instances
                instances = []

    async def write_instances(self, instances):
        """Write or update instances in the MongoDB cache.

        Each instance is upserted (inserted or replaced) based on its
        composite key of ``_anchor_id``, ``_instance_type``, and ``id``.

        Args:
            instances: Iterable of instance dicts to write. Each must contain
                ``_instance_type`` and ``id`` fields.

        Raises:
            django.db.ProgrammingError: If an instance has no ``id`` field.
        """
        for instance in instances:
            instance = _adapt(instance)
            instance['_anchor_id'] = self.anchor_id
            instance_type = instance['_instance_type']
            if instance.get('id', None) is None:
                raise ProgrammingError(f'Instance type {instance_type} has no "id"')
            instance = _adapt(instance)
            await self.collection.replace_one(
                {
                    '_anchor_id': self.anchor_id,
                    '_instance_type': instance['_instance_type'],
                    'id': instance['id'],
                },
                instance,
                upsert=True,
            )

    @staticmethod
    async def clear(channel_class, anchor_id):
        """Delete all cached instances for a given channel and anchor.

        Creates a new MongoDB connection and removes every document matching
        the anchor ID from the channel's collection.

        Args:
            channel_class: The ContextChannel subclass whose collection to clear.
            anchor_id: The anchor ID whose cached state should be deleted.
        """
        client = motor_asyncio.AsyncIOMotorClient(settings.MONGO_URL)
        db = client[settings.MONGO_STATE_DB]
        collection = db[channel_class.__name__.lower()]
        query = {'_anchor_id': anchor_id}
        await collection.delete_many(query)


class MongoSignalWriter:
    """Synchronous MongoDB writer used by Django signal handlers.

    When a model is saved or deleted, the signal handler serializes the
    affected instances and passes them to this writer. The writer updates
    the MongoDB cache and computes deltas (minimal diffs) between the old
    and new state to reduce WebSocket broadcast payload size.

    The connection is lazily established on first use and reused for
    subsequent writes within the same process.

    Attributes:
        channel_class: The ContextChannel subclass this writer belongs to.
        db: The PyMongo database handle (set on first connect).
        collection: The PyMongo collection handle (set on first connect).
    """

    def __init__(self, channel_class):
        """Initialize the signal writer for a channel class.

        Args:
            channel_class: The ContextChannel subclass whose collection
                this writer manages.
        """
        self.channel_class = channel_class
        self.db = None
        self.collection = None

    def connect(self):
        """Establish a synchronous PyMongo connection.

        Creates a new sync client because signal handlers run in Django's
        synchronous context, not in the async event loop.
        """
        client = pymongo.MongoClient(settings.MONGO_URL)
        self.db = client[settings.MONGO_STATE_DB]
        self.collection = self.db[self.channel_class.__name__.lower()]

    def init_database(self):
        """Drop and recreate the collection with required indexes.

        Called during ``post_migrate`` to reset the cache. Creates two indexes:

        - ``instance_pkey``: Composite unique index on (anchor_id, user_key,
          instance_type, id) for fast upserts and lookups.
        - ``reconnection_index``: Index on (anchor_id, tstamp) for efficiently
          fetching updates missed during a client disconnect.
        """
        if self.collection is None:
            self.connect()

        self.collection.drop()

        self.collection.create_index(
            [
                ('_anchor_id', pymongo.ASCENDING),
                ('_user_key', pymongo.ASCENDING),
                ('_instance_type', pymongo.ASCENDING),
                ('id', pymongo.ASCENDING),
            ],
            name='instance_pkey',
        )

        self.collection.create_index(
            [
                ('_anchor_id', pymongo.ASCENDING),
                ('_tstamp', pymongo.DESCENDING),
            ],
            name='reconnection_index',
        )

    def write_instances(self, anchor_id, instances):
        """Write instances to MongoDB and compute deltas for broadcasting.

        For each instance, performs a find-and-update (upsert) to atomically
        replace the cached document and retrieve the previous version. If the
        document exceeds MongoDB's 16MB limit, it is stored in GridFS instead.

        Deltas are computed by comparing old and new documents:

        - New instances (no previous version) are sent in full.
        - Deleted instances are sent in full.
        - Changed instances produce a minimal delta containing only modified fields.

        Args:
            anchor_id: The root object ID scoping these instances.
            instances: Iterable of serialized instance dicts. Each must contain
                ``_instance_type``, ``id``, ``_tstamp``, and ``_operation``.

        Returns:
            list[dict]: Delta documents to broadcast via WebSocket. Each delta
                contains at minimum ``_instance_type``, ``id``, ``_tstamp``,
                and ``_operation``, plus any changed fields.
        """
        if self.collection is None:
            self.connect()

        deltas = []

        for instance in instances:
            instance = _adapt(instance)
            instance['_anchor_id'] = anchor_id
            assert instance['_tstamp']
            try:
                original = self.collection.find_one_and_update(
                    {
                        '_anchor_id': anchor_id,
                        '_instance_type': instance['_instance_type'],
                        'id': instance['id'],
                    }, {
                        '$set': instance,
                    },
                    upsert=True,
                )
                if original:
                    del original['_id']
            except pymongo.errors.DocumentTooLarge:
                original = None
                data = json_dumps(instance).encode()
                fs = gridfs.GridFS(self.db)
                grid_ref = fs.put(data)

                instance = {
                    '_anchor_id': anchor_id,
                    '_instance_type': instance['_instance_type'],
                    'id': instance['id'],
                    '_grid_ref': grid_ref,
                }

                self.collection.replace_one(
                    {
                        '_anchor_id': anchor_id,
                        '_instance_type': instance['_instance_type'],
                        'id': instance['id'],
                    },
                    instance,
                    upsert=True,
                )

            if (original is None
                or instance['_operation'] == 'delete'
                or instance.get('_deleted', False) != original.get('_deleted', False)
                ):
                deltas.append(instance)
            else:
                deltas += delta_utils.generate_delta(original, instance)

        return deltas


def _adapt(instance):
    """Convert Python-specific types to MongoDB-compatible values.

    Transforms ``Decimal`` to ``float`` and ``datetime`` to ISO 8601 strings
    (truncated to microseconds with trailing 'Z'). This ensures all values
    are natively storable in MongoDB documents.

    Args:
        instance: A dict of field name/value pairs.

    Returns:
        dict: A new dict with converted values.
    """
    adapted = {}
    for key, value in instance.items():
        if isinstance(value, Decimal):
            value = float(value)
        elif isinstance(value, datetime):
            value = value.isoformat()[:26] + 'Z'
        adapted[key] = value

    return adapted
