from django.db.models.signals import (post_save, post_delete,
                                      post_migrate, pre_save)
from django.db import transaction
from .mongo import MongoSignalWriter
from .redis import RedisSession, sync_get_tstamp


class SignalHandler:

    def __init__(self, channel_class):
        self.channel_class = channel_class
        self.name = channel_class.name
        self.state_model = channel_class._state_model
        self.wsrouter = channel_class._wsrouter
        self.active = False
        self.mongo = MongoSignalWriter(channel_class)

    def activate(self):
        self.active = True

    def setup(self, app_config):
        if not self.active:
            return

        def init_cache_database(sender, **kwargs):
            self.mongo.init_database()
            RedisSession.init_database(self.channel_class)

        # Cache is deleted on every migrate
        post_migrate.connect(init_cache_database, sender=app_config)

        for model_layer in self.state_model:
            self._connect_layer(model_layer)

    def _connect_layer(self, layer):

        def relay_instance_optimistically(sender, instance, **kwargs):
            if sender is layer.model:
                tstamp = sync_get_tstamp()
                serialized = layer.serialize_instance(instance, tstamp)
                serialized['_operation'] = 'create' if kwargs['created'] else 'update'
                serialized['_optimistic'] = True
                self._schedule(serialized, layer)

        def relay_instance(sender, instance, **kwargs):
            if sender is layer.model:
                tstamp = sync_get_tstamp()
                serialized = layer.serialize_instance(instance, tstamp)
                serialized['_operation'] = 'create' if kwargs['created'] else 'update'
                self._schedule(serialized, layer)

        def relay_delete_instance(sender, instance, **kwargs):
            if sender is layer.model:
                tstamp = sync_get_tstamp()
                serialized = layer.serialize_delete(instance, tstamp)
                self._schedule(serialized, layer)

        uid = '-'.join(['cache', self.name] + layer.instance_path)

        if layer.optimistic:
            pre_save.connect(
                relay_instance_optimistically,
                sender=layer.model,
                dispatch_uid=f'{uid}-optimistic',
            )

        post_save.connect(
            relay_instance,
            sender=layer.model,
            dispatch_uid=f'{uid}-save',
            weak=False,
        )

        post_delete.connect(
            relay_delete_instance,
            sender=layer.model,
            dispatch_uid=f'{uid}-delete',
            weak=False,
        )

    def _schedule(self, serialized, state_model):
        if transaction.get_autocommit():
            self._relay(serialized, state_model)
            return

        transaction.on_commit(
            lambda: self._relay(serialized, state_model)
        )

    def _relay(self, serialized, state_model):
        """Send one update for both cache and connected clients"""
        user_id = serialized.pop('_user_id', None)
        payload = [serialized]

        for anchor in state_model.get_anchors(serialized):
            self.wsrouter.sync_dispatch(payload, anchor.id, user_id)
            self.mongo.write_instances(anchor.id, payload)
