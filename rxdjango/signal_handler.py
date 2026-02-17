import channels.layers
from asgiref.sync import async_to_sync
from collections import defaultdict
from django.db.models.signals import (pre_save, post_save,
                                      pre_delete, post_delete,
                                      post_migrate)
from django.db import transaction, models, ProgrammingError
from django.db.models import Model
from .mongo import MongoSignalWriter
from .redis import RedisSession, sync_get_tstamp
from .exceptions import RxDjangoBug
from .transaction_manager import TransactionBroadcastManager, PendingBroadcast


class RxMeta():
    """
    RxMeta is a lightweight container object used to store
    per-instance metadata during the lifecycle of Django model
    signals

    Attributes
    ----------
    old_parent : dict
        Stores the old parent object if the parent relationship
        that binds the instance to a channel. This allows the
        signal handler to relay updates for both the new and the
        old parent.

    An `RxMeta` instance is attached dynamically to models
    handled by SignalHandler during save lifecycle.
    """
    def __init__(self):
        self.old_parent = {}


# Monkey patch django.db.models.Model.save_base() to attach a RxMeta instance
# before pre_save signal and to cleanup after post_save.
save_base = Model.save_base


def rx_save_base(self, *args, **kwargs):
    self._rx = RxMeta()
    result = save_base(self, *args, **kwargs)
    del self._rx
    return result


Model.save_base = rx_save_base


class SignalHandler:
    """
    SignalHandler wires Django model signals to a ContextChannel,
    ensuring that model changes are consistently propagated to both
    the persistent cache and connected WebSocket clients.

    Each ContextChannel has a single static SignalHandler instance,
    created by its metaclass.

    Attributes
    ----------
    channel_class : ContextChannel
        The channel class this handler is bound to.
    name : str
        The name of the channel.
    state_model : StateModel
        State model tree that defines which Django models participate.
    wsrouter : WebSocketRouter
        Dispatcher for sending updates to clients.
    mongo : MongoSignalWriter
        Writer that persists instance deltas into MongoDB.
    relay_map : dict[Model -> list[Callable]]
        Mapping from model classes to their relay_instance handlers.
    _setup : bool
        Guard to prevent multiple signal registrations.
    """

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
            self.state_model.clean_active()
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

        def prepare_save(sender, instance, **kwargs):
            # Check if this instance has changed parent
            # If so we need to relay the old and new parent
            if not layer.reverse_acessor or kwargs.get('created', False):
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
                instance._rx.old_parent[layer] = old_parent

        def _schedule_instance(_layer, instance, operation, already_scheduled=None):
            """
            Schedule an instance for broadcast at transaction commit time.

            Instead of serializing immediately (which captures mid-transaction state),
            we store a reference to the instance and serialize at commit time when
            the final committed state is available.

            For autocommit mode (no active transaction), serializes and relays immediately.
            """
            if not instance:
                return
            if already_scheduled is None:
                already_scheduled = set()
            if isinstance(instance, models.Model):
                instances = [instance]
            elif isinstance(instance, models.Manager):
                instances = instance.all()
            else:
                raise ProgrammingError()

            for _instance in instances:
                key = f'{_layer.instance_type}:{_instance.id}'

                if key in already_scheduled:
                    continue

                already_scheduled.add(key)

                # Check if we're in a transaction
                if transaction.get_autocommit():
                    # No transaction - serialize and relay immediately
                    tstamp = sync_get_tstamp()
                    if operation == 'delete':
                        serialized = _layer.serialize_delete(_instance, tstamp)
                    else:
                        serialized = _layer.serialize_instance(_instance, tstamp)
                    serialized['_operation'] = operation
                    self._relay(serialized, _layer)
                else:
                    # In transaction - defer serialization to commit time
                    pending = PendingBroadcast(
                        model_class=_instance.__class__,
                        instance_id=_instance.pk or _instance.id,
                        state_model=_layer,
                        operation=operation,
                    )
                    TransactionBroadcastManager.add(self, pending)

        def relay_instance(sender, instance, **kwargs):
            if sender is layer.model:
                created = kwargs.get('created', None)
                operation = kwargs.get('_operation', 'update' if not created else 'create')
                if operation == 'create':
                    created = True
                _schedule_instance(layer, instance, operation)
                if not layer.origin or not layer.reverse_acessor:
                    return
                try:
                    old_parent = instance._rx.old_parent.get(layer, None)
                except AttributeError:
                    old_parent = None
                if created or old_parent:
                    parent = instance
                    for reverse_acessor in layer.reverse_acessor.split('.'):
                        parent = getattr(parent, reverse_acessor, None)
                    _schedule_instance(layer.origin, parent, 'update')
                    if old_parent:
                        _schedule_instance(layer.origin, old_parent, 'update')

        def prepare_deletion(sender, instance, **kwargs):
            """
            Obtain anchors and serialize prior to deletion.

            For deletions, we must serialize BEFORE the delete happens
            because the instance won't exist at commit time. We store
            the serialized data and anchors on the instance for use
            in the post_delete signal.
            """
            if sender is layer.model:
                tstamp = sync_get_tstamp()
                serialized = layer.serialize_delete(instance, tstamp)
                anchors = layer.get_anchors(serialized)
                instance._anchors = list(anchors)
                instance._serialized = serialized

        def relay_delete_instance(sender, instance, **kwargs):
            """
            Schedule the delete broadcast using pre-computed data.

            Uses the serialized data and anchors stored during pre_delete
            since the instance no longer exists in the database.
            """
            if sender is layer.model:
                serialized = instance._serialized
                anchors = instance._anchors

                if transaction.get_autocommit():
                    # No transaction - relay immediately
                    self._relay(serialized, layer, anchors)
                else:
                    # In transaction - defer to commit time
                    # For deletes, we pass the pre-serialized data since
                    # the instance won't exist at commit time
                    pending = PendingBroadcast(
                        model_class=sender,
                        instance_id=serialized['id'],
                        state_model=layer,
                        operation='delete',
                        anchors=anchors,
                        delete_serialized=serialized,
                    )
                    TransactionBroadcastManager.add(self, pending)

        uid = '-'.join(['cache', self.name] + layer.instance_path)

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
        """
        Legacy method for scheduling pre-serialized data.

        This is kept for backwards compatibility with code that
        pre-serializes data (like deletions). For new code, prefer
        using the TransactionBroadcastManager directly.
        """
        if serialized['id'] is None and serialized['_operation'] == 'create':
            raise RxDjangoBug('Saving instance without id causes data leakage. '
                              'Check stack trace to fix this bug.')
        if transaction.get_autocommit():
            self._relay(serialized, state_model, anchors)
            return

        transaction.on_commit(
            lambda: self._relay(serialized, state_model, anchors)
        )

    def _relay(self, serialized, state_model, anchors=None):
        """Send one update for both cache and connected clients"""
        user_id = serialized.get('_user_key', None)
        payload = [serialized]
        if anchors is None:
            anchors = state_model.get_anchors(serialized)
        for anchor in anchors:
            deltas = self.mongo.write_instances(anchor.id, payload)
            if deltas:
                self.wsrouter.sync_dispatch(deltas, anchor.id, user_id)

    def broadcast_instance(self, anchor_id, instance, operation='update'):
        """
        Broadcast an instance update to all connected clients.

        This method is called externally (not via signals) to trigger
        a broadcast. It uses the same deferred serialization approach
        as the signal handlers to ensure consistency.

        Args:
            anchor_id: The anchor (root object) ID for the channel
            instance: The model instance to broadcast
            operation: 'create', 'update', or 'delete'
        """
        sender = instance.__class__

        # Find the state model layer for this instance type
        state_model_layer = None
        for layer in self.state_model.models():
            if layer.model is sender:
                state_model_layer = layer
                break

        if state_model_layer is None:
            # Model not in this channel's state - fall back to old behavior
            kwargs = {'_operation': operation}
            for relay_instance in self.relay_map[sender]:
                relay_instance(sender, instance, **kwargs)
            return

        # Use deferred serialization
        if transaction.get_autocommit():
            # No transaction - serialize and relay immediately
            tstamp = sync_get_tstamp()
            if operation == 'delete':
                serialized = state_model_layer.serialize_delete(instance, tstamp)
            else:
                serialized = state_model_layer.serialize_instance(instance, tstamp)
            serialized['_operation'] = operation
            self._relay(serialized, state_model_layer)
        else:
            # In transaction - defer serialization to commit time
            pending = PendingBroadcast(
                model_class=sender,
                instance_id=instance.pk or instance.id,
                state_model=state_model_layer,
                operation=operation,
            )
            TransactionBroadcastManager.add(self, pending)
