import json
import redis
from asgiref.sync import async_to_sync
from django.utils.functional import cached_property
from django.conf import settings
from rxdjango.serialize import json_dumps


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
        # Active WebSocket session count
        'sessions',
        # Timestamp when sessions hit 0
        'last_disconnect',
    ]

    def __init__(self, channel, anchor_id):
        self.channel = channel

        self.local_keys = [
            _make_key(channel.name, anchor_id, key)
            for key in self.KEYS
        ]

        [
            self.state,
            self.access_time,
            self.instances,
            self.readers,
            self.instances_trigger,
            self.sessions,
            self.last_disconnect,
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

    async def session_connect(self):
        """Increment the active session count and clear last_disconnect."""
        script = """
        redis.call("INCR", KEYS[6])
        redis.call("DEL", KEYS[7])
        """
        await self.connect()
        fn = self._conn.register_script(script)
        await fn(keys=self.local_keys)

    async def session_disconnect(self):
        """Decrement the active session count. If it reaches 0, record the timestamp."""
        script = """
        local sessions = redis.call("DECR", KEYS[6])
        if sessions <= 0 then
            redis.call("SET", KEYS[6], 0)
            redis.call("SET", KEYS[7], ARGV[1])
        end
        return sessions
        """
        await self.connect()
        tstamp = await self.get_tstamp()
        fn = self._conn.register_script(script)
        return await fn(keys=self.local_keys, args=[tstamp])

    @classmethod
    def init_database(cls, channel_class):
        # This needs to be sync
        conn = _sync_connect()
        for key in conn.scan_iter(f"{channel_class.name}:*"):
            conn.delete(key)


class RedisStateSession(RedisSession):

    def __init__(self, channel, anchor_id):
        super().__init__(channel, anchor_id)
        self.initial_state = None
        self.tstamp = None
        self.written_instances = 0

    async def load(self):
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

            -- Return 1 (HEATING) since client is now in HEATING state
            redis.call("SET", KEYS[2], tstamp)
            return 1
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
        serialized_instances = [json_dumps(instance) for instance in instances]

        await self.connect()
        start = self.written_instances

        for instance in instances:
            serialized = json_dumps(instance)
            await self._conn.rpush(self.instances, serialized)
            self.written_instances += 1

        await self._conn.publish(self.instances_trigger, self.written_instances)
        return self.written_instances

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
        pubsub = self._conn.pubsub(ignore_subscribe_messages=True)
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
                await pubsub.unsubscribe(self.instances_trigger)
                return

            message = await pubsub.get_message(timeout=5)
            if message is not None:
                last_length = int(message['data'])
                instances_length = abs(last_length)
            else:
                # Timeout — check if list grew or if state changed to HOT
                instances_length = await self._conn.llen(self.instances)
                if instances_length == last_length:
                    # No new instances. Check if state is HOT (list is complete).
                    state = await self._conn.get(self.state)
                    if state is not None and int(state) == 2:
                        await pubsub.unsubscribe(self.instances_trigger)
                        return
                    continue
                last_length = instances_length


    _end_session_methods = [
        end_cold_session,
        end_heating_session,
        end_hot_session,
        end_heating_session,  # COOLING clients transition to HEATING
    ]

    async def end(self, success):
        method = self._end_session_methods[self.initial_state]
        return await method(self, success)

    async def start_cooling(self):
        """Atomically transition HOT → COOLING unconditionally.

        Used by clear_cache for manual/programmatic cache clearing.

        Returns True if transition occurred, False if state is not HOT.
        """
        script = """
        local state = tonumber(redis.call("GET", KEYS[1])) or 0
        if state ~= 2 then
            return 0
        end

        -- HOT → COOLING
        redis.call("SET", KEYS[1], 3)
        redis.call("DEL", KEYS[3])
        redis.call("SET", KEYS[4], 0)
        return 1
        """
        await self.connect()
        fn = self._conn.register_script(script)
        result = await fn(keys=self.local_keys)
        return result > 0

    async def start_cooling_if_stale(self, ttl):
        """Atomically transition HOT → COOLING if sessions==0 and TTL expired.

        Returns True if transition occurred, False otherwise.
        """
        script = """
        local state = tonumber(redis.call("GET", KEYS[1])) or 0
        if state ~= 2 then
            return 0
        end

        local sessions = tonumber(redis.call("GET", KEYS[6])) or 0
        if sessions > 0 then
            return 0
        end

        local last_disconnect = tonumber(redis.call("GET", KEYS[7]))
        if not last_disconnect then
            return 0
        end

        local now = tonumber(ARGV[1])
        local ttl = tonumber(ARGV[2])
        if (now - last_disconnect) < ttl then
            return 0
        end

        -- HOT → COOLING
        redis.call("SET", KEYS[1], 3)
        redis.call("DEL", KEYS[3])
        redis.call("SET", KEYS[4], 0)
        return 1
        """
        await self.connect()
        now = await self.get_tstamp()
        fn = self._conn.register_script(script)
        result = await fn(keys=self.local_keys, args=[now, ttl])
        return result > 0

    async def finish_cooling(self):
        """Atomically finish the COOLING process.

        Returns:
            0 if COOLING → COLD (success, done)
            1 if state changed to HEATING (client connected, need reheat)
            -1 if state is unexpected
        """
        script = """
        local state = tonumber(redis.call("GET", KEYS[1])) or 0
        if state == 3 then
            -- Still COOLING. Signal end, go COLD.
            local len = tonumber(redis.call("LLEN", KEYS[3])) or 0
            if len > 0 then
                redis.call("PUBLISH", KEYS[5], -len)
            end
            redis.call("SET", KEYS[1], 0)
            redis.call("DEL", KEYS[3])
            redis.call("SET", KEYS[4], 0)
            return 0
        elseif state == 1 then
            -- HEATING: client connected during COOLING. Signal end, need reheat.
            local len = tonumber(redis.call("LLEN", KEYS[3])) or 0
            if len > 0 then
                redis.call("PUBLISH", KEYS[5], -len)
            end
            return 1
        end
        return -1
        """
        await self.connect()
        fn = self._conn.register_script(script)
        return await fn(keys=self.local_keys)

