import asyncio
from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from django.conf import settings
from .redis import RedisStateSession
from .mongo import MongoStateSession
from .exceptions import AnchorDoesNotExist

if settings.DEBUG or settings.TESTING:
    def mark(instances, cache_state):
        if instances:
            instances[0]['_cache_state'] = cache_state
        return instances
else:
    def mark(instances, cache_state):
        return instances


class StateLoader:
    """Manages the cache synchronization process using Redis.
    Cache has 4 possible states, and each time a client access it, a transition
    may occur. These are:

    - 0 COLD: No cache, need to get from db
    - 1 HEATING: Cache is being built, peers can follow at redis
    - 2 HOT: Cache is available, everyone gets from mongo
    - 3 COOLING: Cache is being deleted, first peer can re-heat from redis

    Attributes:
        name (str): The name of the cache.
        anchor_id (int): The anchor ID associated with the cache.
        base_key (str): The base key used for all Redis keys related to this anchor ID.
    """

    def __init__(self, channel, anchor_id):
        self.channel = channel
        self.state_model = channel._state_model

        self.redis = RedisStateSession(channel, anchor_id)
        self.mongo = MongoStateSession(channel, anchor_id)

        self.anchor_id = anchor_id
        self.user_id = channel.user_id

        self.cache_state = None
        self.tstamp = None

    async def __aenter__(self):
        self.cache_state = await self.redis.load()
        self.tstamp = self.redis.tstamp
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.redis.end(exc_type is None)

        if exc_type is not None:
            raise exc

    def _user_filter(self, instances):
        """Filter out other user's instances.
        During COLD, HEATING and COOLING loader has to deal with all users
        """
        return [
            i for i in instances if i.pop('_user_key') in (self.user_id, None)
        ]

    async def _list_instances_cold(self):
        """In COLD state, fetch all instances from database,
        write to mongo and redis and send instances filtered by user
        """
        # Clean any stale MongoDB data (e.g. from signal writes during COOLING)
        await MongoStateSession.clear(type(self.channel), self.anchor_id)
        anchor = await self._get_anchor_from_db()
        iterator = self.state_model.serialize_state(anchor, self.tstamp)

        @database_sync_to_async
        def next_instances():
            return next(iterator, None)

        while True:
            instances = await next_instances()
            if instances is None:
                await self.redis.end_write()
                break
            if len(instances) == 0:
                continue
            await asyncio.gather(
                self.mongo.write_instances(instances),
                self.redis.write_instances(instances),
            )

            instances = self._user_filter(instances)
            if instances:
                yield mark(instances, 'cold')

    @database_sync_to_async
    def _get_anchor_from_db(self):
        Anchor = self.state_model.model
        active_flag = self.state_model.active_flag
        if active_flag:
            kwargs = { active_flag: True }
            Anchor.objects.filter(id=self.anchor_id).update(**kwargs)
        try:
            return Anchor.objects.get(id=self.anchor_id)
        except Anchor.DoesNotExist:
            raise AnchorDoesNotExist(
                f'{Anchor.__name__} id {self.anchor_id} does not exist'
            )

    async def _list_instances_heating(self):
        """In HEATING state, fetch all instances from redis and
        send instances filtered by user.
        """
        async for instances in self.redis.list_instances():
            instances = self._user_filter(instances)
            yield mark(instances, 'heating')

    async def _list_instances_hot(self):
        """In HOT state, fetch filtered instances from mongo and send"""
        async for instances in self.mongo.list_instances(self.user_id):
            yield mark(instances, 'hot')

    router = [
        _list_instances_cold,
        _list_instances_heating,
        _list_instances_hot,
        None,  # COOLING clients transition to HEATING via load()
    ]

    async def list_instances(self):
        """All instances that should go to a user"""
        if self.cache_state is None:
            raise Exception("Generator must be called inside with block")

        list_method = self.router[self.cache_state]

        async for instances in list_method(self):
            yield instances
