import json
from datetime import datetime
from copy import copy
from decimal import Decimal
import pymongo
from motor import motor_asyncio
from django.db import ProgrammingError
from django.conf import settings
from .redis import get_tstamp, sync_get_tstamp


class MongoStateSession:

    def __init__(self, channel):
        self.channel = channel
        self.anchor_id = channel.anchor_id
        self.user_id = channel.user_id
        self.state_model = channel._state_model
        self._tstamp = None

        client = motor_asyncio.AsyncIOMotorClient(settings.MONGO_URL)
        db = client[settings.MONGO_STATE_DB]
        self.collection = db[channel.__class__.__name__.lower()]

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
                del instance['_id']
                del instance['_anchor_id']
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


class MongoSignalWriter:
    def __init__(self, channel_class):
        client = pymongo.MongoClient(settings.MONGO_URL)
        db = client[settings.MONGO_STATE_DB]
        self.collection = db[channel_class.__name__.lower()]

    def init_database(self):
        # Make a new connection, because this needs to be sync
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
        for instance in instances:
            instance = _adapt(instance)
            instance['_anchor_id'] = anchor_id
            assert instance['_tstamp']
            self.collection.replace_one(
                {
                    '_anchor_id': anchor_id,
                    '_instance_type': instance['_instance_type'],
                    'id': instance['id'],
                },
                instance,
                upsert=True,
            )
            pass


def _adapt(instance):
    adapted = {}
    for key, value in instance.items():
        if isinstance(value, Decimal):
            value = float(value)
        elif isinstance(value, datetime):
            value = value.isoformat()[:26] + 'Z'
        adapted[key] = value

    return adapted
