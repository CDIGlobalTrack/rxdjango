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

    def __init__(self, channel, anchor_id):
        self.channel = channel
        self.anchor_id = anchor_id
        self.user_id = channel.user_id
        self.state_model = channel._state_model
        self._tstamp = None

        client = motor_asyncio.AsyncIOMotorClient(settings.MONGO_URL)
        self.db = client[settings.MONGO_STATE_DB]
        self.collection = self.db[channel.__class__.__name__.lower()]

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

    @staticmethod
    async def clear(channel_class, anchor_id):
        client = motor_asyncio.AsyncIOMotorClient(settings.MONGO_URL)
        db = client[settings.MONGO_STATE_DB]
        collection = db[channel_class.__name__.lower()]
        query = {'_anchor_id': anchor_id}
        await collection.delete_many(query)


class MongoSignalWriter:
    def __init__(self, channel_class):
        self.channel_class = channel_class
        self.db = None
        self.collection = None

    def connect(self):
        # Make a new connection, because this needs to be sync
        client = pymongo.MongoClient(settings.MONGO_URL)
        self.db = client[settings.MONGO_STATE_DB]
        self.collection = self.db[self.channel_class.__name__.lower()]

    def init_database(self):
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

            if 'TriggerSerializer' in instance['_instance_type']:
                print(f'DEBUG TRIGGER {instance}')

            if (original is None
                or instance['_operation'] == 'delete'
                or instance.get('_deleted', False) != original.get('_deleted', False)
                ):
                deltas.append(instance)
            else:
                deltas += delta_utils.generate_delta(original, instance)

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
