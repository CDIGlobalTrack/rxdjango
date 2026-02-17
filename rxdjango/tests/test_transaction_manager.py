"""
Tests for TransactionBroadcastManager.

These tests verify that the deferred serialization mechanism works correctly:
- Multiple saves of the same instance result in a single broadcast
- Serialization happens at commit time with the final state
- Thread isolation works correctly
"""
import unittest
from unittest.mock import Mock, patch, MagicMock
from django.db import transaction
from django.test import TestCase, TransactionTestCase

from rxdjango.transaction_manager import (
    TransactionBroadcastManager,
    PendingBroadcast,
)


class TestPendingBroadcast(unittest.TestCase):
    """Tests for PendingBroadcast class."""

    def test_delete_operation_uses_preserialized_data(self):
        """Delete operations should use pre-serialized data, not fetch from DB."""
        mock_handler = Mock()
        mock_handler._relay = Mock()

        mock_state_model = Mock()
        mock_anchors = [Mock()]

        delete_serialized = {
            'id': 123,
            '_instance_type': 'test.TestSerializer',
            '_operation': 'delete',
            '_deleted': True,
        }

        pending = PendingBroadcast(
            model_class=Mock,
            instance_id=123,
            state_model=mock_state_model,
            operation='delete',
            anchors=mock_anchors,
            delete_serialized=delete_serialized,
        )

        pending.serialize_and_relay(mock_handler, tstamp=1234567890.123)

        # Should call _relay with the pre-serialized data
        mock_handler._relay.assert_called_once()
        call_args = mock_handler._relay.call_args
        serialized = call_args[0][0]
        assert serialized['id'] == 123
        assert serialized['_tstamp'] == 1234567890.123
        assert serialized['_deleted'] is True

    def test_update_operation_fetches_fresh_from_db(self):
        """Update operations should fetch fresh instance from DB."""
        mock_handler = Mock()
        mock_handler._relay = Mock()

        mock_state_model = Mock()
        mock_state_model.serialize_instance = Mock(return_value={
            'id': 123,
            'name': 'Updated Name',
        })

        mock_model_class = Mock()
        mock_instance = Mock()
        mock_instance.pk = 123
        mock_model_class.objects.get = Mock(return_value=mock_instance)

        pending = PendingBroadcast(
            model_class=mock_model_class,
            instance_id=123,
            state_model=mock_state_model,
            operation='update',
        )

        pending.serialize_and_relay(mock_handler, tstamp=1234567890.123)

        # Should fetch from DB
        mock_model_class.objects.get.assert_called_once_with(pk=123)
        # Should serialize the fetched instance
        mock_state_model.serialize_instance.assert_called_once_with(
            mock_instance, 1234567890.123
        )
        # Should relay
        mock_handler._relay.assert_called_once()


class TestTransactionBroadcastManager(unittest.TestCase):
    """Tests for TransactionBroadcastManager class."""

    def setUp(self):
        """Clear any pending broadcasts before each test."""
        TransactionBroadcastManager._clear()

    def tearDown(self):
        """Clear pending broadcasts after each test."""
        TransactionBroadcastManager._clear()

    def test_deduplication_same_instance(self):
        """Multiple adds for same instance should keep only the last one."""
        mock_handler = Mock()
        mock_handler.name = 'test_channel'

        mock_state_model = Mock()
        mock_state_model.instance_type = 'test.TestSerializer'

        # Add first pending broadcast
        pending1 = PendingBroadcast(
            model_class=Mock,
            instance_id=123,
            state_model=mock_state_model,
            operation='update',
        )

        # Add second pending broadcast for same instance
        pending2 = PendingBroadcast(
            model_class=Mock,
            instance_id=123,
            state_model=mock_state_model,
            operation='update',
        )

        with patch.object(transaction, 'on_commit'):
            TransactionBroadcastManager.add(mock_handler, pending1)
            TransactionBroadcastManager.add(mock_handler, pending2)

        # Should only have one pending broadcast
        assert TransactionBroadcastManager.pending_count() == 1

    def test_different_instances_not_deduplicated(self):
        """Different instances should not be deduplicated."""
        mock_handler = Mock()
        mock_handler.name = 'test_channel'

        mock_state_model = Mock()
        mock_state_model.instance_type = 'test.TestSerializer'

        pending1 = PendingBroadcast(
            model_class=Mock,
            instance_id=123,
            state_model=mock_state_model,
            operation='update',
        )

        pending2 = PendingBroadcast(
            model_class=Mock,
            instance_id=456,
            state_model=mock_state_model,
            operation='update',
        )

        with patch.object(transaction, 'on_commit'):
            TransactionBroadcastManager.add(mock_handler, pending1)
            TransactionBroadcastManager.add(mock_handler, pending2)

        # Should have two pending broadcasts
        assert TransactionBroadcastManager.pending_count() == 2

    def test_on_commit_registered_only_once(self):
        """on_commit should only be called once per transaction."""
        mock_handler = Mock()
        mock_handler.name = 'test_channel'

        mock_state_model = Mock()
        mock_state_model.instance_type = 'test.TestSerializer'

        pending1 = PendingBroadcast(
            model_class=Mock,
            instance_id=123,
            state_model=mock_state_model,
            operation='update',
        )

        pending2 = PendingBroadcast(
            model_class=Mock,
            instance_id=456,
            state_model=mock_state_model,
            operation='update',
        )

        with patch.object(transaction, 'on_commit') as mock_on_commit:
            TransactionBroadcastManager.add(mock_handler, pending1)
            TransactionBroadcastManager.add(mock_handler, pending2)

        # on_commit should be called only once
        mock_on_commit.assert_called_once()


if __name__ == '__main__':
    unittest.main()
