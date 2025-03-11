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


class MongoStateSession:

    def __init__(self, channel, anchor_id):
        self.channel = channel
        self.anchor_id = anchor_id
        self.user_id = channel.user_id
        self.state_model = channel._state_model
        self._tstamp = None

        client = motor_asyncio.AsyncIOMotorClient(settings.MONGO_URL)
        self.db = client[settings.MONGO_STATE_DB]
        name = channel.__class__.__name__.lower()
        self.collection = self.db[name]
        self.atime_collection = self.db[f'{name}_atime']

    async def tstamp(self):
        if self._tstamp:
            return self._tstamp
        self._tstamp = await get_tstamp()
        return self._tstamp

    async def list_instances(self, user_id):
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

    async def write_atime(self, atime):
        """Saves the access time of this anchor in a collection,
        so that cache control can be implemented"""
        await self.atime_collection.replace_one(
            { '_anchor_id': self.anchor_id },
            {
                '_anchor_id': self.anchor_id,
                '_tstamp': self.tstamp,
            },
            upsert=True,
        )

    @staticmethod
    async def clear(channel_class, anchor_id):
        client = motor_asyncio.AsyncIOMotorClient(settings.MONGO_URL)
        db = client[settings.MONGO_STATE_DB]
        channel_class.__name__.lower()
        collection = db[name]
        atime_collection = db[f'{name}_atime']
        query = {'_anchor_id': anchor_id}
        await collection.delete_many(query)
        await atime_collection.delete_one(query)

    @staticmethod
    async def list_cached_anchors(channel_class, query={}):
        client = motor_asyncio.AsyncIOMotorClient(settings.MONGO_URL)
        db = client[settings.MONGO_STATE_DB]
        channel_class.__name__.lower()
        atime_collection = db[f'{name}_atime']
        async for instance in atime_collection.find(query):
            yield instance['_anchor_id'], instance['_tstamp']


class MongoSignalWriter:
    def __init__(self, channel_class):
        # Make a new connection, because this needs to be sync
        client = pymongo.MongoClient(settings.MONGO_URL)
        self.db = client[settings.MONGO_STATE_DB]
        name = channel.__class__.__name__.lower()
        self.collection = self.db[name]
        self.atime_collection = self.db[f'{name}_atime']

    def init_database(self):
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

        self.atime_collection.create_index(
            [
                ('_anchor_id', pymongo.ASCENDING),
                ('_tstamp', pymongo.DESCENDING),
            ],
            name='anchor_index',
        )

    def write_instances(self, anchor_id, instances):
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

            if original is None or instance['_operation'] == 'delete':
                deltas.append(instance)
            else:
                empty = True
                for key, old_value in original.items():
                    if key == 'id' or key.startswith('_'):
                        continue
                    try:
                        new_value = instance[key]
                    except KeyError:
                        # An exception in a property may have generated
                        # an incomplete serialized object.
                        # TODO emit a warning
                        continue
                    if isinstance(new_value, list):
                        old_value = set(old_value)
                        new_value = set(new_value)
                    if new_value == old_value:
                        del instance[key]
                    else:
                        empty = False

                if not empty:
                    deltas.append(instance)

        return deltas


def _adapt(instance):
    adapted = {}
    for key, value in instance.items():
        if isinstance(value, Decimal):
            value = float(value)
        elif isinstance(value, datetime):
            value = value.isoformat()[:26] + 'Z'
        adapted[key] = value

    return adapted
