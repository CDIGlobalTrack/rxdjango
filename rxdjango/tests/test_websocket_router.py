"""
Tests for rxdjango.websocket_router module.

Tests the channel key generation and WebsocketRouter class.
"""
import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from rxdjango.websocket_router import (
    SYSTEM_CHANNEL,
    WebsocketRouter,
    get_channel_key,
    send_system_message,
)


def _run(coro):
    """Run an async coroutine in a fresh event loop."""
    return asyncio.run(coro)


class TestGetChannelKey:
    """Tests for the get_channel_key function."""

    def test_without_user_id(self):
        """Key without user_id should be name_anchor."""
        assert get_channel_key('mychannel', 42) == 'mychannel_42'

    def test_with_user_id(self):
        """Key with user_id should be name_anchor_user."""
        assert get_channel_key('mychannel', 42, 7) == 'mychannel_42_7'

    def test_user_id_none_same_as_omitted(self):
        """Explicit None should produce same result as omitting user_id."""
        assert get_channel_key('ch', 1, None) == get_channel_key('ch', 1)

    def test_string_ids(self):
        """Should work with string IDs (e.g., UUIDs)."""
        result = get_channel_key('ch', 'abc-123', 'user-456')
        assert result == 'ch_abc-123_user-456'


class TestSystemChannel:
    """Tests for the SYSTEM_CHANNEL constant."""

    def test_default_system_channel_name(self):
        """Default system channel should be _rxdjango_system."""
        assert SYSTEM_CHANNEL == '_rxdjango_system'


class TestWebsocketRouter:
    """Tests for the WebsocketRouter class."""

    def test_init_stores_name(self):
        """Router should store the channel name."""
        router = WebsocketRouter('test_channel')
        assert router.name == 'test_channel'

    def test_connect_adds_to_three_groups(self):
        """connect should add consumer to anchor, user, and system groups."""
        router = WebsocketRouter('test_channel')
        mock_layer = AsyncMock()

        _run(router.connect(mock_layer, 'consumer_1', 42, 7))

        assert mock_layer.group_add.call_count == 3

        # Check the group names
        calls = [call.args[0] for call in mock_layer.group_add.call_args_list]
        assert 'test_channel_42' in calls      # anchor group
        assert 'test_channel_42_7' in calls     # user group
        assert SYSTEM_CHANNEL in calls          # system group

    def test_disconnect_removes_from_three_groups(self):
        """disconnect with user_id should remove from all three groups."""
        router = WebsocketRouter('test_channel')
        mock_layer = AsyncMock()

        _run(router.disconnect(mock_layer, 'consumer_1', 42, 7))

        assert mock_layer.group_discard.call_count == 3

    def test_disconnect_without_user_removes_from_two_groups(self):
        """disconnect without user_id should remove from anchor and system only."""
        router = WebsocketRouter('test_channel')
        mock_layer = AsyncMock()

        _run(router.disconnect(mock_layer, 'consumer_1', 42))

        assert mock_layer.group_discard.call_count == 2
        calls = [call.args[0] for call in mock_layer.group_discard.call_args_list]
        assert 'test_channel_42' in calls
        assert SYSTEM_CHANNEL in calls

    def test_dispatch_sends_to_correct_group(self):
        """dispatch should send payload to the correct channel group."""
        router = WebsocketRouter('test_channel')

        mock_layer = AsyncMock()
        with patch('rxdjango.websocket_router.channels.layers.get_channel_layer',
                   return_value=mock_layer):
            _run(router.dispatch({'id': 1, 'name': 'test'}, 42))

        mock_layer.group_send.assert_called_once()
        call_args = mock_layer.group_send.call_args
        assert call_args[0][0] == 'test_channel_42'
        assert call_args[0][1]['type'] == 'relay'

    def test_dispatch_with_user_id(self):
        """dispatch with user_id should send to user-specific group."""
        router = WebsocketRouter('test_channel')

        mock_layer = AsyncMock()
        with patch('rxdjango.websocket_router.channels.layers.get_channel_layer',
                   return_value=mock_layer):
            _run(router.dispatch({'id': 1}, 42, user_id=7))

        call_args = mock_layer.group_send.call_args
        assert call_args[0][0] == 'test_channel_42_7'

    def test_sync_dispatch_uses_async_wrapper(self):
        """sync_dispatch should bridge the async dispatch method."""
        router = WebsocketRouter('test_channel')

        with patch('rxdjango.websocket_router.async_to_sync') as mock_async_to_sync:
            sync_callable = Mock()
            mock_async_to_sync.return_value = sync_callable

            router.sync_dispatch({'id': 1}, 42, user_id=7)

        mock_async_to_sync.assert_called_once_with(router.dispatch)
        sync_callable.assert_called_once_with({'id': 1}, 42, 7)


class TestSendSystemMessage:
    """Tests for system message broadcasting."""

    def test_sends_payload_to_system_group(self):
        mock_layer = AsyncMock()

        with patch('rxdjango.websocket_router.channels.layers.get_channel_layer',
                   return_value=mock_layer):
            _run(send_system_message('tests', {'text': 'hello'}))

        mock_layer.group_send.assert_awaited_once_with(
            SYSTEM_CHANNEL,
            {
                'type': 'relay',
                'payload': {
                    'source': 'tests',
                    'message': {'text': 'hello'},
                },
            },
        )
