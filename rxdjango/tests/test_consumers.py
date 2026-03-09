"""
Tests for rxdjango.consumers module.

Tests the @consumer decorator, consumer method collection,
and StateConsumer message handling logic.
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

import rxdjango.consumers as consumers_module
from rxdjango.consumers import StateConsumer, consumer, get_consumer_methods

CONSUMERS = getattr(consumers_module, '__CONSUMERS')


def _run(coro):
    """Run an async coroutine in a fresh event loop."""
    return asyncio.run(coro)


class TestConsumerDecorator:
    """Tests for the @consumer decorator."""

    def setup_method(self):
        """Save and clear global consumer registry."""
        self._orig = CONSUMERS.copy()

    def teardown_method(self):
        """Restore global consumer registry."""
        CONSUMERS.clear()
        CONSUMERS.update(self._orig)

    def test_decorator_registers_method(self):
        """@consumer should register the method in the global registry."""
        @consumer('chat.message')
        async def handle_chat(self, event):
            pass

        key = f'{handle_chat.__module__}.{handle_chat.__qualname__}'
        assert key in CONSUMERS
        event_type, func = CONSUMERS[key]
        assert event_type == 'chat.message'
        assert func is handle_chat

    def test_decorator_requires_string_argument(self):
        """@consumer must receive a string event_type."""
        with pytest.raises(TypeError, match="must be a string"):
            @consumer(123)
            async def bad_handler(self, event):
                pass

    def test_decorator_requires_callable(self):
        """@consumer(group) must decorate a callable."""
        with pytest.raises(TypeError, match="is a decorator"):
            consumer('my.event')("not_a_callable")

    def test_decorator_returns_original_function(self):
        """@consumer should return the original function unchanged."""
        @consumer('test.event')
        async def my_handler(self, event):
            pass

        assert callable(my_handler)
        assert my_handler.__name__ == 'my_handler'

    def test_multiple_decorators(self):
        """Multiple @consumer decorated methods should all be registered."""
        @consumer('event.one')
        async def handler_one(self, event):
            pass

        @consumer('event.two')
        async def handler_two(self, event):
            pass

        key1 = f'{handler_one.__module__}.{handler_one.__qualname__}'
        key2 = f'{handler_two.__module__}.{handler_two.__qualname__}'
        assert key1 in CONSUMERS
        assert key2 in CONSUMERS


class TestGetConsumerMethods:
    """Tests for get_consumer_methods function."""

    def setup_method(self):
        self._orig = CONSUMERS.copy()

    def teardown_method(self):
        CONSUMERS.clear()
        CONSUMERS.update(self._orig)

    def test_collects_matching_methods(self):
        """Should find @consumer methods belonging to the class."""
        # Simulate what @consumer does - register with full qualname
        async def handle_event(self, event):
            pass

        class FakeChannel:
            __module__ = 'myapp.channels'
            __qualname__ = 'FakeChannel'

        key = 'myapp.channels.FakeChannel.handle_event'
        CONSUMERS[key] = ('custom.event', handle_event)

        result = get_consumer_methods(FakeChannel)
        assert 'handle_event' in result
        event_type, func = result['handle_event']
        assert event_type == 'custom.event'

    def test_pops_from_global_registry(self):
        """get_consumer_methods should pop entries from __CONSUMERS."""
        async def handler(self, event):
            pass

        class MyChannel:
            __module__ = 'app.channels'
            __qualname__ = 'MyChannel'

        key = 'app.channels.MyChannel.handler'
        CONSUMERS[key] = ('my.event', handler)

        get_consumer_methods(MyChannel)

        # Should be popped from global
        assert key not in CONSUMERS

    def test_ignores_unrelated_methods(self):
        """Methods from other classes should not be collected."""
        async def other_handler(self, event):
            pass

        class TargetChannel:
            __module__ = 'app.channels'
            __qualname__ = 'TargetChannel'

        key = 'app.channels.OtherChannel.other_handler'
        CONSUMERS[key] = ('other.event', other_handler)

        result = get_consumer_methods(TargetChannel)
        assert len(result) == 0
        # Should still be in registry
        assert key in CONSUMERS


class TestStateConsumer:
    """Tests for StateConsumer message routing helpers."""

    def test_receive_routes_authentication_before_user(self):
        consumer = StateConsumer()
        consumer.user = None
        consumer.receive_authentication = AsyncMock()
        consumer.receive_action = AsyncMock()

        _run(consumer.receive('{"token": "abc"}'))

        consumer.receive_authentication.assert_awaited_once_with({'token': 'abc'})
        consumer.receive_action.assert_not_called()

    def test_receive_routes_actions_after_user(self):
        consumer = StateConsumer()
        consumer.user = object()
        consumer.receive_authentication = AsyncMock()
        consumer.receive_action = AsyncMock()

        _run(consumer.receive('{"action": "ping", "params": [], "callId": 1}'))

        consumer.receive_action.assert_awaited_once_with(
            {'action': 'ping', 'params': [], 'callId': 1}
        )
        consumer.receive_authentication.assert_not_called()

    def test_receive_invalid_json_disconnects_and_reraises(self):
        consumer = StateConsumer()
        consumer.disconnect = AsyncMock()

        with pytest.raises(json.JSONDecodeError):
            _run(consumer.receive('{bad json'))

        consumer.disconnect.assert_awaited_once_with()

    def test_end_of_data_uses_current_timestamp(self):
        consumer = StateConsumer()
        consumer.tstamp = 123.45

        payload = json.loads(consumer.end_of_data)
        assert payload == [{
            '_instance_type': '',
            '_tstamp': 123.45,
            '_operation': 'end_initial_state',
            'id': 0,
        }]

    def test_send_connection_status_closes_on_error(self):
        consumer = StateConsumer()
        consumer.send = AsyncMock()

        _run(consumer.send_connection_status(401, 'error/unauthorized'))

        consumer.send.assert_awaited_once()
        kwargs = consumer.send.await_args.kwargs
        assert kwargs['close'] is True
        assert json.loads(kwargs['text_data']) == {
            'statusCode': 401,
            'error': 'error/unauthorized',
        }

    def test_prepend_anchor_id_sends_payload(self):
        consumer = StateConsumer()
        consumer.send = AsyncMock()

        _run(consumer.prepend_anchor_id(7))

        consumer.send.assert_awaited_once_with(text_data='{"prependAnchor": 7}')

    def test_receive_action_sends_result(self):
        consumer = StateConsumer()
        consumer.channel = object()
        consumer.send = AsyncMock()

        with patch('rxdjango.consumers.execute_action', new=AsyncMock(return_value={'ok': True})):
            _run(consumer.receive_action({
                'callId': 3,
                'action': 'doThing',
                'params': ['x'],
            }))

        consumer.send.assert_awaited_once()
        payload = json.loads(consumer.send.await_args.kwargs['text_data'])
        assert payload == {'callId': 3, 'result': {'ok': True}}

    def test_receive_action_sends_error_and_reraises(self):
        consumer = StateConsumer()
        consumer.channel = object()
        consumer.send = AsyncMock()

        with patch(
            'rxdjango.consumers.execute_action',
            new=AsyncMock(side_effect=RuntimeError('boom')),
        ):
            with pytest.raises(RuntimeError, match='boom'):
                _run(consumer.receive_action({
                    'callId': 9,
                    'action': 'explode',
                    'params': [],
                }))

        consumer.send.assert_awaited_once()
        payload = json.loads(consumer.send.await_args.kwargs['text_data'])
        assert payload == {'callId': 9, 'error': 'Error'}
