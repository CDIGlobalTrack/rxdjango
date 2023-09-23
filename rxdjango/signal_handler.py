from collections import defaultdict
from django.db.models.signals import (pre_save, post_save,
                                      pre_delete, post_delete,
                                      post_migrate)
from django.db import transaction
from .mongo import MongoSignalWriter
from .redis import RedisSession, sync_get_tstamp


class SignalHandler:

    def __init__(self, channel_class):
        self.channel_class = channel_class
        self.name = channel_class.name
        self.state_model = channel_class._state_model
        self.wsrouter = channel_class._wsrouter
        self.mongo = MongoSignalWriter(channel_class)
        self.relay_map = defaultdict(list)

        self._setup = False

    def setup(self, app_config):
        if self._setup:
            return
        self._setup = True

        def init_cache_database(sender, **kwargs):
            self.mongo.init_database()
            RedisSession.init_database(self.channel_class)

        # Cache is deleted on every migrate
        post_migrate.connect(
            init_cache_database,
            sender=app_config,
            weak=False,
        )

        for model_layer in self.state_model.models():
            self._connect_layer(model_layer)

    def _connect_layer(self, layer):

        def relay_instance_optimistically(sender, instance, **kwargs):
            if sender is layer.model:
                tstamp = sync_get_tstamp()
                serialized = layer.serialize_instance(instance, tstamp)
                created = kwargs.get('created', None)
                serialized['_operation'] = 'create' if created else 'update'
                serialized['_optimistic'] = True
                self._schedule(serialized, layer)

        def relay_instance(sender, instance, **kwargs):
            if sender is layer.model:
                tstamp = sync_get_tstamp()
                serialized = layer.serialize_instance(instance, tstamp)
                created = kwargs.get('created', None)
                serialized['_operation'] = 'create' if created else 'update'
                self._schedule(serialized, layer)

        def prepare_deletion(sender, instance, **kwargs):
            """Obtain anchors prior to deletion and store in instance"""
            if sender is layer.model:
                tstamp = sync_get_tstamp()
                serialized = layer.serialize_delete(instance, tstamp)
                anchors = layer.get_anchors(serialized)
                instance._anchors = list(anchors)
                instance._serialized = serialized

        def relay_delete_instance(sender, instance, **kwargs):
            if sender is layer.model:
                serialized = instance._serialized
                self._schedule(serialized, layer, instance._anchors)

        uid = '-'.join(['cache', self.name] + layer.instance_path)
        from account.models import User
        if layer.model is User:
            pass #import ipdb; ipdb.set_trace()

        if layer.optimistic:
            pre_save.connect(
                relay_instance_optimistically,
                sender=layer.model,
                dispatch_uid=f'{uid}-optimistic',
                weak=False,
            )

        post_save.connect(
            relay_instance,
            sender=layer.model,
            dispatch_uid=f'{uid}-save',
            weak=False,
        )

        pre_delete.connect(
            prepare_deletion,
            sender=layer.model,
            dispatch_uid=f'{uid}-prepare-deletion',
            weak=False,
        )

        post_delete.connect(
            relay_delete_instance,
            sender=layer.model,
            dispatch_uid=f'{uid}-delete',
            weak=False,
        )

        self.relay_map[layer.model].append(relay_instance)

    def _schedule(self, serialized, state_model, anchors=None):
        if transaction.get_autocommit():
            self._relay(serialized, state_model, anchors)
            return

        transaction.on_commit(
            lambda: self._relay(serialized, state_model, anchors)
        )

    def _relay(self, serialized, state_model, anchors):
        """Send one update for both cache and connected clients"""
        user_id = serialized.pop('_user_id', None)
        payload = [serialized]
        #print(f'Relay from {serialized["_instance_type"]}')
        if anchors is None:
            anchors = state_model.get_anchors(serialized)
        for anchor in anchors:
            #print(f'... to {anchor.__class__.__name__} {anchor}')
            self.wsrouter.sync_dispatch(payload, anchor.id, user_id)
            self.mongo.write_instances(anchor.id, payload)

    def broadcast_instance(self, anchor_id, instance, operation='update'):
        kwargs = {}
        if operation == 'created':
            kwargs['created'] = True
        sender = instance.__class__
        for relay_instance in self.relay_map[sender]:
            relay_instance(sender, instance, **kwargs)
