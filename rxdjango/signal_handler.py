import channels.layers
from asgiref.sync import async_to_sync
from collections import defaultdict
from django.db.models.signals import (pre_save, post_save,
                                      pre_delete, post_delete,
                                      post_migrate)
from django.db import transaction, models, ProgrammingError
from .mongo import MongoSignalWriter
from .redis import RedisSession, sync_get_tstamp
from .exceptions import RxDjangoBug


class SignalHandler:
    """SignalHandler is responsible for registering signals
    on all models that are part of the state of a ContextChannel.
    Each ContextChannel class has a static instance of SignalHandler,
    created by the metaclass.
    """

    def __init__(self, channel_class):
        self.channel_class = channel_class
        self.meta = channel_class.meta
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

        if self.channel_class.meta.auto_update:
            self._connect_anchor_events()

    def _connect_layer(self, layer):
        """Register signals for models of this layer"""
        def relay_instance_optimistically(sender, instance, **kwargs):
            if sender is layer.model:
                tstamp = sync_get_tstamp()
                if not instance.id:
                    # Doesn't matter to optimistically send new object if
                    # referring instance is not updated
                    # This can be implemented by sending a patch on the parent,
                    # using only the id, without hitting the database
                    return

                serialized = layer.serialize_instance(instance, tstamp)
                serialized['_operation'] = 'update'
                serialized['_optimistic'] = True
                self._schedule(serialized, layer)

        def prepare_save(sender, instance, **kwargs):
            # Check if this instance has changed parent
            # If so we need to relay the old and new parent
            if not layer.reverse_acessor:
                return
            if kwargs.get('created', False):
                instance.__parent_updated = True
                return
            try:
                current = sender.objects.get(pk=instance.pk or instance.id)
            except sender.DoesNotExist:
                return # Should not happen, but who knows
            acessor = layer.reverse_acessor
            try:
                old_parent = getattr(current, acessor)
            except AttributeError as e:
                # This is a bug in RxDjango.
                # While we don't fix it, let's not break things
                return
            new_parent = getattr(instance, acessor)
            if new_parent != old_parent:
                instance.__parent_updated = True
                instance.__old_parent = old_parent

        def _relay_instance(_layer, instance, tstamp, operation, already_relayed=None):
            if not instance:
                return
            if already_relayed is None:
                already_relayed = set()
            if isinstance(instance, models.Model):
                instances = [instance]
            elif isinstance(instance, models.Manager):
                instances = instance.all()
            else:
                raise ProgrammingError()

            for _instance in instances:
                if operation == 'delete':
                    serialized = _layer.serialize_delete(_instance, tstamp)
                else:
                    serialized = _layer.serialize_instance(_instance, tstamp)
                serialized['_operation'] = operation
                key = f'{_layer.instance_type}:{_instance.id}'
                if key in already_relayed:
                    continue
                already_relayed.add(key)

                self._schedule(serialized, _layer)

                if False and operation == 'create':
                    # This has been disabled because it broadcast peers recursively,
                    # making simple things like creating a project take a long time
                    # when there are a lot of projects in a customer.
                    # The original intention of this block was to move together all
                    # children and an instance changed parent, we need another way.
                    # ----
                    # If instance is being created in this channel,
                    # then all related objects need to be scheduled
                    for attribute, child_layer in _layer.children.items():
                        child = getattr(_instance, attribute, None)
                        if child is None:
                            continue
                        elif isinstance(child, models.QuerySet):
                            children = child.all()
                        else:
                            children = [child]

                        for child in children:
                            try:
                                if child.id is None:
                                    continue
                            except AttributeError:
                                pass
                            _relay_instance(child_layer, child, tstamp, operation, already_relayed)

        def relay_instance(sender, instance, **kwargs):
            if sender is layer.model:
                tstamp = sync_get_tstamp()
                created = kwargs.get('created', None)
                operation = kwargs.get('_operation', 'update' if not created else 'create')
                if operation == 'create':
                    created = True
                _relay_instance(layer, instance, tstamp, operation)
                if not layer.origin or not layer.reverse_acessor:
                    return
                if created or getattr(instance, '__parent_updated', False):
                    parent = instance
                    for reverse_acessor in layer.reverse_acessor.split('.'):
                        parent = getattr(parent, reverse_acessor, None)
                    _relay_instance(layer.origin, parent, tstamp, 'update')
                    old_pa = getattr(instance, '__old_parent', None)
                    _relay_instance(layer.origin, old_pa, tstamp, 'update')

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

        if layer.optimistic:
            pre_save.connect(
                relay_instance_optimistically,
                sender=layer.model,
                dispatch_uid=f'{uid}-optimistic',
                weak=False,
            )

        pre_save.connect(
            prepare_save,
            sender=layer.model,
            dispatch_uid=f'{uid}-prepare-save',
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

    def _connect_anchor_events(self):
        """Add signal to broadcast creation and deletion of instances"""
        anchor_model = self.channel_class.meta.state.child.Meta.model
        channel_layer = channels.layers.get_channel_layer()

        def add_to_list(sender, instance, **kwargs):
            if sender is not anchor_model or not kwargs.get('created', False):
                return
            async_to_sync(channel_layer.group_send)(
                self.channel_class._anchor_events_channel,
                {
                    'type': 'instances.list.add',
                    'instance_id': instance.id,
                },
            )
        def remove_from_list(sender, instance, **kwargs):
            if sender is not anchor_model:
                return
            async_to_sync(channel_layer.group_send)(
                self.channel_class._anchor_events_channel,
                {
                    'type': 'instances.list.remove',
                    'instance_id': instance._serialized['id'],
                },
            )

        post_save.connect(
            add_to_list,
            sender=anchor_model,
            dispatch_uid=f'{anchor_model.__name__}-list-add',
            weak=False,
        )
        post_delete.connect(
            remove_from_list,
            sender=anchor_model,
            dispatch_uid=f'{anchor_model.__name__}-list-remove',
            weak=False,
        )

    def _schedule(self, serialized, state_model, anchors=None):
        if serialized['id'] is None and serialized['_operation'] == 'create':
            raise RxDjangoBug('Saving instance without id causes data leakage. '
                              'Check stack trace to fix this bug.')
        if transaction.get_autocommit():
            self._relay(serialized, state_model, anchors)
            return

        transaction.on_commit(
            lambda: self._relay(serialized, state_model, anchors)
        )

    def _relay(self, serialized, state_model, anchors):
        """Send one update for both cache and connected clients"""
        user_id = serialized.get('_user_key', None)
        payload = [serialized]
        if anchors is None:
            anchors = state_model.get_anchors(serialized)
        for anchor in anchors:
            deltas = self.mongo.write_instances(anchor.id, payload)
            from django.conf import settings
            if anchor.__class__.__name__ == 'Run':
                from django.utils import timezone
                from inspection.tasks import log_run_state
                log_run_state.delay(anchor.id)
                fh = open('/tmp/signal.log', 'a')
                fh.write(f'{timezone.now()} - Run {anchor.id} - {len(deltas)} {anchor.use_magnet}\n')
                fh.close()

            if deltas or not getattr(settings, 'RX_USE_DELTAS', True):
                self.wsrouter.sync_dispatch(deltas, anchor.id, user_id)

    def broadcast_instance(self, anchor_id, instance, operation='update'):
        kwargs = {
            '_operation': operation
        }
        sender = instance.__class__
        for relay_instance in self.relay_map[sender]:
            relay_instance(sender, instance, **kwargs)
