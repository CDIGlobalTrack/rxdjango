import json
import redis
from asgiref.sync import async_to_sync
from django.utils.functional import cached_property
from django.conf import settings


async def _connect():
    return await redis.asyncio.from_url(settings.REDIS_URL)

def _sync_connect():
    return redis.from_url(settings.REDIS_URL)


async def get_tstamp():
    conn = await _connect()
    tstamp = await conn.time()
    return _make_tstamp(tstamp)

def sync_get_tstamp():
    conn = _sync_connect()
    tstamp = conn.time()
    return _make_tstamp(tstamp)


def is_active(channel_class, anchor_id):
    key = _make_key(channel_class.name, anchor_id, 'state');
    conn = _sync_connect()
    # Return true if state is HEATING or HOT
    return conn.get(key) in (1, 2)


def _make_tstamp(tstamp):
    return tstamp[0] + tstamp[1] / 1000000

def _make_key(channel_name, anchor_id, key):
    return f'{channel_name}:{anchor_id}:{key}'



class RedisSession:

    # These are the variables for each anchor_id in Redis
    KEYS = [
        # The cache state - 0 COLD, 1 HEATING, 2 HOT or 3 COOLING
        'state',
        # The latest access time
        'access_time',
        # A list with all serialized instances, available
        # during HEATING and COOLING
        'instances',
        # The number of clients reading the list, to know when to cleanup
        'readers',
        # A pub/sub to notify on new instances as the list is built.
        # It's fed with the size of the list.
        'instances_trigger',
        # The number of clients relaying instances to the database
        'relay_writers',
        # A pub/sub to notify that relay_writers has changed
        'relay_writers_trigger',
    ]

    def __init__(self, channel):
        self.channel = channel

        self.local_keys = [
            _make_key(channel.name, channel.anchor_id, key)
            for key in self.KEYS
        ]

        [
            self.state,
            self.access_time,
            self.instances,
            self.readers,
            self.instances_trigger,
            self.relay_writers,
            self.relay_writers_trigger,
        ] = self.local_keys

        self._conn = None

    async def connect(self):
        if self._conn:
            return self._conn
        self._conn = await _connect()
        return self._conn

    async def get_tstamp(self):
        tstamp = await self._conn.time()
        return _make_tstamp(tstamp)

    @classmethod
    def init_database(cls, channel_class):
        # This needs to be sync
        conn = _sync_connect()
        for key in conn.scan_iter(f"{channel_class.name}:*"):
            conn.delete(key)


class RedisStateSession(RedisSession):

    def __init__(self, channel):
        super().__init__(channel)
        self.initial_state = None
        self.tstamp = None

    async def start(self):
        """Make an atomic call to redis, check the state and maybe transition

        States are:
          COLD
            - Documents are not cached.
            - Transition to HEATING
            - Clear the Redis instance list and set readers to 0
            - State come from ORM and go to Redis and Mongo
          HEATING
            - Documents are being cached
            - Increment readers
            - State come from Redis
          HOT
            - Documents are cached
            - State come from Mongo
          COOLING
            - Documents are being deleted from Mongo and availabe in Redis
            - Transition to HEATING
            - Increment readers
            - State come from Redis and go back go Mongo

        access_time is always updated

        Returns:
            int: The current state, represented as one of the class constants.
        """
        # 1 state,  2 access_time, 3 instances, 4 readers
        script = """
        local state = tonumber(redis.call("GET", KEYS[1])) or 0
        local tstamp = ARGV[1]

        if state == 0 then
            -- COLD state. Transition to HEATING
            redis.call("SET", KEYS[1], 1)

            -- clear instances list and reset readers
            redis.call("DEL", KEYS[3])
            redis.call("SET", KEYS[4], 0)

        elseif state == 1 then
            -- HEATING state. Increment readers
            redis.call("INCR", KEYS[4])

        elseif state == 3 then
            -- COOLING state. Transition to HEATING
            redis.call("SET", KEYS[1], 1)

            -- update readers to 1, as we're first reader
            redis.call("SET", KEYS[4], 1)
        end

        -- Update access timestamp
        redis.call("SET", KEYS[2], tstamp)

        return state
        """
        await self.connect()

        self.tstamp = await self.get_tstamp()

        start_session = self._conn.register_script(script)

        self.initial_state = await start_session(
            keys=self.local_keys,
            args=[self.tstamp],
        )

        return self.initial_state


    async def end_cold_session(self, success):
        """End a COLD session

        Transition state to HOT and delete the instances key if readers is 0.
        """
        if not success:
            return await self.rollback_to_cold()

        # 1 state,  2 access_time, 3 instances, 4 readers
        script = """
        local readers = tonumber(redis.call("GET", KEYS[4])) or 0

        if readers == 0 then
            -- delete instances key
            redis.call("DEL", KEYS[3])
        end

        -- transition to HOT
        redis.call("SET", KEYS[1], 2)

        return readers
        """
        await self.connect()
        end_cold_session = self._conn.register_script(script)
        return await end_cold_session(keys=self.local_keys)

    async def end_heating_session(self, success):
        """End a HEATING session

        Decrement readers and delete the instances key if readers reaches 0.
        """
        # 1 state,  2 access_time, 3 instances, 4 readers
        script = """
        local readers = tonumber(redis.call("DECR", KEYS[4])) or 0

        if readers == 0 then
            redis.call("DEL", KEYS[3]) -- delete instances key if readers is 0
        end

        return readers
        """
        await self.connect()
        end_heating_session = self._conn.register_script(script)
        return await end_heating_session(keys=self.local_keys)

    async def end_hot_session(self, success):
        """HOT session doesn't change anything"""
        pass

    async def end_cooling_session(self, success):
        """End COOLING session.

        Since we setup readers at 1, this is the same as end_heating_session.
        """
        await self.connect()
        return await self.end_heating_session(success)

    async def rollback_to_cold(self):
        """Transition to COLD, and broadcast error message to readers"""
        # 1 state,  2 access_time, 3 instances, 4 readers, 5 trigger, 6 lock, 7 lock_release
        script = """
        local readers = tonumber(redis.call("GET", KEYS[4])) or 0

        if readers > 0 then
            local error_message = 'error'
            redis.call("RPUSH", KEYS[3], error_message)
            local instances_size = redis.call("LLEN", KEYS[3]) -- Get the updated size of instances
            redis.call("PUBLISH", KEYS[5], instances_size) -- Publish the size to trigger channel
        end

        redis.call("SET", KEYS[1], 0) -- transition to COLD

        return readers
        """
        await self.connect()
        rollback_to_cold = self._conn.register_script(script)
        return await rollback_to_cold(keys=self.local_keys)


    async def write_instances(self, instances):
        """Serialize a list of objects, append them to the instances list, and publish the size to the trigger pub/sub channel.

        Args:
            objects (list): List of objects to append to the instances list.
        """
        serialized_instances = [json.dumps(instance, default=str) for instance in instances]

        script = """
        -- Append the serialized instances to instances list
        for i, obj in ipairs(ARGV) do
            redis.call("RPUSH", KEYS[3], obj)
        end

        local instances_size = redis.call("LLEN", KEYS[3]) -- Get the updated size of instances
        redis.call("PUBLISH", KEYS[5], instances_size) -- Publish the size to trigger channel

        return instances_size
        """
        await self.connect()
        write_instances = self._conn.register_script(script)
        return await write_instances(
            keys=self.local_keys,
            args=serialized_instances,
        )

    async def end_write(self):
        """Notify all readers with a negative length, or delete instances if no readers"""

        script = """
        local readers = tonumber(redis.call("GET", KEYS[4])) or 0
        local negative_size = -tonumber(redis.call("LLEN", KEYS[3]))

        if readers == 0 then
            -- No readers, just clean instances
            redis.call("DEL", KEYS[3])
            return 0
        else
            -- Publish negative size to trigger
            redis.call("PUBLISH", KEYS[5], negative_size)
            return negative_size
        end
        """
        end_write = self._conn.register_script(script)
        return await end_write(keys=self.local_keys)

    async def list_instances(self):
        """An async generator that yields all instances available, then awaits on trigger and yields all new instances.

        When a negative value comes, it raises StopAsyncIteration after yielding the instances.
        """
        await self.connect()
        pubsub = self._conn.pubsub()
        await pubsub.subscribe(self.instances_trigger)
        # Get the initial length of instances and set up a cursor
        try:
            instances_length = await self._conn.llen(self.instances)
        except redis.exceptions.ResponseError:
            instances_length = 0
            await self._conn.delete(self.instances)

        last_length = 0
        cursor = 0

        while True:
            # If there are new instances, fetch and yield them using LRANGE
            if cursor < instances_length:
                new_instances = await self._conn.lrange(
                    self.instances, cursor, instances_length - 1
                )

                cursor = instances_length

                yield [json.loads(serialized) for serialized in new_instances]

            if last_length < 0:
                await self._conn.unsubscribe(self.instances_trigger)
                raise StopAsyncIteration

            message = await pubsub.get_message()
            if message is not None:
                last_length = int(message['data'])
                instances_length = abs(last_length)
            else:
                # Why do we get empty message?
                instances_length = await self._conn.llen(self.instances)
                if instances_length == last_length:
                    continue
                last_length = instances_length


    _end_session_methods = [
        end_cold_session,
        end_heating_session,
        end_hot_session,
        end_cooling_session,
    ]

    async def end(self, success):
        method = self._end_session_methods[self.initial_state]
        return await method(self, success)


class RedisRelaySession:

    async def start(self):
        pass

    async def acquire_write_lock(self, timeout=None):
        script = """
        local state = tonumber(redis.call("GET", KEYS[1])) or 0

        if state == 0 or state == 3 then
            -- COLD or COOLING, no write should be done
            return 0
        end

        if state == 1 then
            -- HEATING, wait for finish
            return -1
        end

        local lock = tonumber(redis.call("GET", KEYS[6])) or 0
        if lock < 0 then
            -- Cleanup, wait for finish
            return -1
        end

        -- Increment lock
        redis.call("INCR", KEYS[6])
        redis.call("SET", KEYS[6], lock + 1) -- acquire lock

        return 1
        """
        # Subscribe to lock_release
        await self.connect()
        _, consumer = await self._conn.subscribe(self.relay_writers_trigger)

        acquire_write_lock = self._conn.register_script(script)
        while True:
            acquired = await acquire_write_lock(keys=self.local_keys)

            if acquired >= 0:
                await self._conn.unsubscribe(self.relay_writers_trigger)
                return acquired > 0

            if timeout:
                try:
                    await asyncio.wait_for(consumer.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    await self._conn.unsubscribe(self.relay_writers_trigger)
                    return False
            else:
                await consumer.get()

    async def release_write_lock(self):
        script = """
        local writers = tonumber(redis.call("DECR", KEYS[4])) or 0
        redis.call("PUBLISH", KEYS[7], 1)
        end
        """
        release_write_lock = self._conn.register_script(script)
        await release_write_lock(keys=self.local_keys)

    async def acquire_cleanup_lock(self):
        script = """
        -- Set the lock key to 0 to publish to release pub/sub
        local writers = tonumber(redis.call("GET", KEYS[6])) or 0
        if writers < 0 then
            -- Another cleanup job running, do nothing
            return 0
        end
        if writers == 0 then
            redis.call("SET", KEYS[6], -1)
            redis.call("PUBLISH", KEYS[7], 1)
            return 1
        end

        -- Someone is writing, wait for release
        return -1
        """
        await self.connect()
        # Subscribe to lock_release
        _, consumer = await self._conn.subscribe(self.relay_writers_trigger)

        acquire_cleanup_lock = self._conn.register_script(script)
        while True:
            acquired = await acquire-cleanup_lock(keys=self.local_keys)

            if acquired >= 0:
                await self._conn.unsubscribe(self.relay_writers_trigger)
                return acquired > 0

            if timeout:
                try:
                    await asyncio.wait_for(consumer.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    await self._conn.unsubscribe(self.relay_writers_trigger)
                    return False
            else:
                await consumer.get()


    async def release_cleanup_lock(self):
        script = """
        redis.call("SET", KEYS[6], 0)
        redis.call("PUBLISH", KEYS[7], 1)
        end
        """
        release_cleanup_lock = self._conn.register_script(script)
        await release_cleanup_lock(keys=self.local_keys)
