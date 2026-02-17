"""
Transaction-scoped broadcast manager for RxDjango.

This module provides deferred serialization and deduplication of broadcasts
within a database transaction. Instead of serializing instance state when
save() is called (which may capture stale mid-transaction state), it stores
instance references and serializes them at commit time when the final
committed state is available.

Key features:
- Deduplication: Multiple saves of the same instance result in a single broadcast
- Deferred serialization: Instance state is captured at commit time, not save time
- Thread-safe: Uses thread-local storage for concurrent transaction support
"""
import threading
import logging
from django.db import transaction

logger = logging.getLogger(__name__)


class PendingBroadcast:
    """
    Stores information needed to broadcast an instance, deferring
    serialization until transaction commit time.

    For regular updates/creates, we store the model class and instance ID,
    then fetch fresh data at commit time.

    For deletions, we store the pre-computed serialized data and anchors
    (since the instance won't exist after commit).
    """

    def __init__(self, model_class, instance_id, state_model, operation,
                 anchors=None, delete_serialized=None):
        """
        Args:
            model_class: The Django model class
            instance_id: The instance primary key
            state_model: The StateModel layer for serialization
            operation: 'create', 'update', or 'delete'
            anchors: Pre-computed anchors (required for delete)
            delete_serialized: Pre-serialized data for delete operations
        """
        self.model_class = model_class
        self.instance_id = instance_id
        self.state_model = state_model
        self.operation = operation
        self.anchors = anchors
        self.delete_serialized = delete_serialized

    def serialize_and_relay(self, handler, tstamp):
        """
        Serialize the instance and relay to cache/websocket.

        For delete operations, uses pre-computed serialized data.
        For other operations, fetches fresh instance from DB.
        """
        if self.operation == 'delete':
            # Use pre-computed serialized data for deletions
            serialized = self.delete_serialized
            serialized['_tstamp'] = tstamp
            handler._relay(serialized, self.state_model, self.anchors)
            return

        # Fetch fresh instance from DB (transaction has committed)
        try:
            instance = self.model_class.objects.get(pk=self.instance_id)
        except self.model_class.DoesNotExist:
            # Instance was deleted after being queued but before commit
            # This can happen if delete() is called after save() in same tx
            logger.debug(
                f"Instance {self.model_class.__name__}:{self.instance_id} "
                "no longer exists at commit time, skipping broadcast"
            )
            return

        serialized = self.state_model.serialize_instance(instance, tstamp)
        serialized['_operation'] = self.operation
        handler._relay(serialized, self.state_model, self.anchors)


class TransactionBroadcastManager:
    """
    Manages pending broadcasts for the current transaction.

    Uses thread-local storage to maintain separate pending broadcast
    queues for different threads (and thus different transactions).

    Usage:
        # In signal handler, instead of immediately serializing:
        TransactionBroadcastManager.add(handler, pending_broadcast)

        # At transaction commit, all pending broadcasts are flushed:
        # - Instances are fetched fresh from DB
        # - Serialized with a single timestamp
        # - Relayed to cache and websocket
    """
    _local = threading.local()

    @classmethod
    def _get_state(cls):
        """Get or initialize thread-local state."""
        if not hasattr(cls._local, 'pending'):
            cls._local.pending = {}
            cls._local.handlers = {}
            cls._local.registered = False
        return cls._local

    @classmethod
    def add(cls, handler, pending_broadcast):
        """
        Add a pending broadcast to the current transaction's queue.

        If multiple broadcasts for the same instance are added, only the
        last one is kept (deduplication). The on_commit callback is
        registered only once per transaction.

        Args:
            handler: The SignalHandler instance
            pending_broadcast: PendingBroadcast containing instance info
        """
        state = cls._get_state()

        # Key by (handler_name, instance_type, instance_id)
        # This ensures deduplication per channel and per instance
        key = (
            handler.name,
            pending_broadcast.state_model.instance_type,
            pending_broadcast.instance_id,
        )

        # Store the pending broadcast (last one wins for same key)
        state.pending[key] = pending_broadcast
        state.handlers[handler.name] = handler

        # Register on_commit callback only once per transaction
        if not state.registered:
            state.registered = True
            transaction.on_commit(cls._flush)

    @classmethod
    def _flush(cls):
        """
        Flush all pending broadcasts at transaction commit time.

        This is called by Django's on_commit hook. It:
        1. Gets a single timestamp for all broadcasts (consistency)
        2. Fetches fresh instance data from DB
        3. Serializes and relays each instance
        4. Clears the pending queue
        """
        state = cls._get_state()

        if not state.pending:
            cls._clear()
            return

        # Import here to avoid circular imports
        from .redis import sync_get_tstamp

        # Single timestamp for all broadcasts in this transaction
        tstamp = sync_get_tstamp()

        # Process all pending broadcasts
        for key, pending in state.pending.items():
            handler_name = key[0]
            handler = state.handlers.get(handler_name)
            if handler:
                try:
                    pending.serialize_and_relay(handler, tstamp)
                except Exception as e:
                    logger.exception(
                        f"Error broadcasting {pending.model_class.__name__}:"
                        f"{pending.instance_id}: {e}"
                    )

        cls._clear()

    @classmethod
    def _clear(cls):
        """Clear the pending queue and reset registration flag."""
        state = cls._get_state()
        state.pending = {}
        state.handlers = {}
        state.registered = False

    @classmethod
    def has_pending(cls):
        """Check if there are pending broadcasts (useful for testing)."""
        state = cls._get_state()
        return bool(state.pending)

    @classmethod
    def pending_count(cls):
        """Get count of pending broadcasts (useful for testing)."""
        state = cls._get_state()
        return len(state.pending)
