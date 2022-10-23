import json
from collections import deque
from pymongo import MongoClient
from django.utils import timezone
from hashlib import md5
from asgiref.sync import async_to_sync
import redis
import channels.layers
from django.conf import settings
from django.db import connection, transaction
from django.db.models.signals import post_save, post_delete
from rest_framework import serializers
from wsframework.consumers import ModelSetConsumer
from wsframework.tasks import clean_transaction
from wsframework.mongo import log_transaction
from wsframework.exceptions import ModelNotRegistered, ProgrammingError


# FIXME: This is postgresql dependent.
# Also, we should support operations in autocommit mode (not inside transaction)
def get_transaction_id():
    with connection.cursor() as cursor:
        cursor.execute("select txid_current()")
        return cursor.fetchone()[0]


class ModelSetChannel:

    @property
    def snapshot(self):
        raise NotImplementedError

    @property
    def model(self):
        return self.snapshot.Meta.model

    snapshot_cache_key = None
    supported_models = []

    def has_permission(self, user, instance_id):
        """Implement this method to check if user has permission on a channel"""
        return True

    async def initialize(self, user, instance_id, transaction):
        """Called after initial data has been sent (snapshot or updates)"""
        pass

    async def complement_snapshot(self, user, instance_id):
        for i in []:
            yield i

    async def initialize_snapshot(self, snapshot):
        """Modify snapshot right before it's sent through websocket"""
        return snapshot

    async def finish(self, user, instance_id):
        pass

    def __init__(self):
        if not issubclass(self.snapshot, serializers.ModelSerializer):
            raise ProgrammingError(f'{Model.__name__}.snapshot must inherit from serializers.ModelSerializer')

        Model = self.snapshot.Meta.model

        self.key = '_'.join([
            Model.__module__.replace('.models', '').replace('.', '_'),
            Model.__name__.lower(),
        ])

        self._connect()
        self.serializers = {}

        self.register_model(Model, self.snapshot)

        for Serializer in self.pieces:
            Model = Serializer.Meta.model
            self.register_model(Model, Serializer)

    @classmethod
    def as_asgi(klass):
        Consumer = type(
            f'{klass.__name__}Consumer',
            (ModelSetConsumer,),
            dict(model_set_channel=klass),
        )
        return Consumer.as_asgi()

    def register_model(self, Model, Serializer):
        instance_type = Model.__name__
        assert Serializer.Meta.model is Model
        self.serializers[instance_type] = Serializer

    def get_serializer(self, instance):
        instance_type = instance.__class__.__name__
        return self.serializers[instance_type]

    def broadcast_instance(self, channel_instance, instance, operation='update'):
        update = self._create_update(instance, operation)

        self._broadcast_payload(channel_instance, [ update ])


    def broadcast_queryset(self, channel_instance, qs, operation='update'):
        updates = [ self._create_update(i, operation) for i in qs ]
        if not updates:
            return

        self._broadcast_payload(channel_instance, updates)


    def _create_update(self, instance, operation):
        instance_type = instance.__class__.__name__
        serializer_class = self.serializers[instance_type]
        data = serializer_class(instance).data

        update = dict(
            operation=operation,
            instance_type=instance_type,
            instance_id=instance.id,
            data=data,
        )
        return json.loads(json.dumps(update, default=str))

    def _broadcast_payload(self, channel_instance, updates):
        payload = {
            'payload': updates,
            'transaction': self._transaction_id(channel_instance.id),
        }

        def send():
            self.send(channel_instance.id, payload)

        if settings.TESTING:
            send()
        else:
            transaction.on_commit(send)

    def broadcast_user_updates(self, model, get_channel_instance, get_user):
        self.broadcast_updates(model, get_channel_instance, get_user)

    def broadcast_updates(self, model, get_channel_instance, get_user=None):
        instance_type = model.__name__
        try:
            serializer_class = self.serializers[instance_type]
        except KeyError:
            raise ModelNotRegistered(instance_type)
        serializer_name = serializer_class.__name__
        fingerprint = md5(get_channel_instance.__code__.co_code).hexdigest()[:8]

        def get_user_id(instance):
            if not get_user:
                return
            user_id = get_user(instance)
            if isinstance(user_id, int):
                return user_id
            return user_id.id

        self.serializers[instance_type] = serializer_class

        uid = f'{serializer_name}-{instance_type}-{fingerprint}'

        def on_post_save(sender, instance, **kwargs):
            channel_instance = get_channel_instance(instance)
            operation = 'create' if kwargs['created'] else 'update'
            data = serializer_class(instance).data
            user_id = get_user_id(instance)

            data = json.dumps(data, default=str)
            if channel_instance:
                self._enqueue(channel_instance_id=channel_instance.id,
                              operation=operation,
                              instance_type=instance_type,
                              instance_id=instance.id,
                              user_id=user_id)

        post_save.connect(on_post_save,
                          sender=model,
                          dispatch_uid=f'{uid}-update',
                          weak=False)

        def on_post_delete(sender, instance, **kwargs):
            channel_instance = get_channel_instance(instance)
            user_id = get_user_id(instance)
            if channel_instance:
                self._enqueue(channel_instance_id=channel_instance.id,
                              operation='delete',
                              instance_type=instance_type,
                              instance_id=instance.id,
                              user_id=user_id)

        post_delete.connect(on_post_delete,
                            sender=model,
                            dispatch_uid=f'{uid}-delete',
                            weak=False)

    def broadcast_related_updates(self, model, get_channel_instance, get_related):
        parent_model = model.__name__
        fingerprint = md5(get_channel_instance.__code__.co_code + get_related.__code__.co_code).hexdigest()[:8]

        uid = f'{parent_model}-{fingerprint}'

        def on_post_save(sender, instance, **kwargs):
            for related_instance in get_related(instance):
                channel_instance = get_channel_instance(related_instance)
                instance_type = related_instance.__class__.__name__
                serializer_class = self.serializers[instance_type]
                data = serializer_class(related_instance).data
                data = json.dumps(data, default=str)
                self._enqueue(channel_instance_id=channel_instance.id,
                              operation='update',
                              instance_type=instance_type,
                              instance_id=related_instance.id)


        post_save.connect(on_post_save,
                          sender=model,
                          dispatch_uid=f'{uid}-update',
                          weak=False)

        post_delete.connect(on_post_save,
                            sender=model,
                            dispatch_uid=f'{uid}-delete',
                            weak=False)

    def _connect(self):
        """Connect to Redis"""
        self._conn = redis.Redis(host=settings.REDIS_HOST,
                                 port=settings.REDIS_PORT,
                                 db=settings.REDIS_DB)

    def _queue(self, channel_instance_id, transaction_id=None, user_id=None):
        """Return the queue name for this transaction and instance id"""

        if transaction_id is None:
            transaction_id = get_transaction_id()

        keys = [
            settings.BUFFER_QUEUE_PREFIX,
            self.key,
            str(channel_instance_id),
            str(transaction_id),
        ]
        if user_id is not None:
            keys.append(str(user_id))

        return '-'.join(keys)

    def _transaction_id(self, channel_instance_id):
        # Attention, there are two things called transaction.
        # This is the GT's output transaction, coming from redis
        key = '-'.join([
            settings.TRANSACTION_ID_PREFIX,
            self.key,
            str(channel_instance_id),
        ])
        return self._conn.incr(key)

    def _length(self, queue):
        """Return the size of the queue for a channel_key"""
        try:
            return self._conn.llen(queue)
        except redis.exceptions.ConnectionError:
            self._connect()
            return self._conn.llen(queue)

    def _enqueue(self, channel_instance_id, operation, instance_type,
                 instance_id, in_transaction=True, user_id=None):
        queue = self._queue(channel_instance_id, user_id=user_id)

        if not self._length(queue):
            # Setup new queue
            transaction_id = get_transaction_id()
            if not transaction_id == get_transaction_id():
                raise Exception("Broadcast operation should be inside a "
                                "transaction.atomic block")
            transaction.on_commit(lambda: self.flush_transaction(channel_instance_id,
                                                                 transaction_id,
                                                                 user_id))
            timeout = settings.TRANSACTION_TIMEOUT
            clean_transaction.apply_async((queue,), countdown=timeout)

        update = dict(
            operation=operation,
            instance_type=instance_type,
            instance_id=instance_id,
        )

        self._conn.rpush(queue, json.dumps(update))

    # Attention, there are two things called transaction.
    # This is the input transaction from the postgres database
    def flush_transaction(self, channel_instance_id, transaction_id=None, user_id=None):
        if transaction_id is None:
            transaction_id = get_transaction_id()
        queue = self._queue(channel_instance_id, transaction_id, user_id)
        size = self._length(queue)
        if not size:
            return
        pipe = self._conn.pipeline()
        for i in range(size):
            pipe.lpop(queue)

        updates = [json.loads(up) for up in pipe.execute()]
        # We want to only take the last update for each object, so we
        # process in reverse order and appendleft on a deque, ignoring
        # objects that have already been passed
        payload = deque()
        index = {}
        for update in reversed(updates):
            instance_type = update['instance_type']
            instance_id = update['instance_id']
            operation = update['operation']

            key = f'{instance_type}:{instance_id}'

            if index.get(key):
                latest = index[key]
                if operation == 'update' and latest['operation'] == 'create':
                    latest['operation'] = 'create'
                continue

            if operation == 'delete':
                data = instance_id
            else:
                Serializer = self.serializers[instance_type]
                Model = Serializer.Meta.model
                try:
                    instance = Model.objects.get(id=instance_id)
                except Model.DoesNotExist:
                    # Instance has been deleted by another process
                    continue

                data = json.loads(json.dumps(Serializer(instance).data, default=str))

            update['data'] = data

            index[key] = update
            payload.appendleft(update)

        self.send(channel_instance_id, {
            'transaction': self._transaction_id(channel_instance_id),
            'payload': list(payload),
        })

    def send(self, channel_instance_id, payload):
        async_to_sync(self.dispatch)(channel_instance_id, payload)
        log_transaction(self.key, channel_instance_id, payload)

    async def dispatch(self, channel_instance_id, payload):
        channel_key = f'{self.key}_{channel_instance_id}'
        return await self._dispatch(channel_key, payload)

    async def dispatch_user(self, channel_instance_id, user_id, payload):
        channel_key = f'{self.key}_{channel_instance_id}_{user_id}'
        return await self._dispatch(channel_key, payload)

    async def _dispatch(self, channel_key, payload):
        channel_layer = channels.layers.get_channel_layer()

        await channel_layer.group_send(
            channel_key,
            {
                'type': 'broadcast',
                'payload': payload,
            },
        )
