"""Tests for MongoStateSession query construction.

These tests verify that MongoStateSession.list_instances applies the correct
MongoDB query filters, particularly the `last_update` timestamp filter used
for efficient WebSocket reconnection.
"""
import asyncio
from unittest.mock import MagicMock, patch


def _run(coro):
    """Run an async coroutine in a fresh event loop."""
    return asyncio.run(coro)


class AsyncIterator:
    """Mock async iterator simulating a Motor cursor with no results."""
    def __init__(self, items=()):
        self.items = iter(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.items)
        except StopIteration:
            raise StopAsyncIteration


def make_session(anchor_id=5):
    """Create a MongoStateSession with mocked Motor client and collection."""
    channel = MagicMock()
    channel.user_id = 42
    channel.__class__.__name__ = 'TestChannel'

    mock_model = MagicMock()
    mock_model.instance_type = 'test.Serializer'
    channel._state_model.models.return_value = [mock_model]

    with patch('rxdjango.mongo.motor_asyncio.AsyncIOMotorClient'):
        from rxdjango.mongo import MongoStateSession
        session = MongoStateSession(channel, anchor_id)

    session.collection = MagicMock()
    session.collection.find.return_value = AsyncIterator()
    return session


async def consume(agen):
    """Drain an async generator."""
    async for _ in agen:
        pass


class TestMongoStateSessionListInstances:
    """Tests for MongoStateSession.list_instances last_update filtering."""

    def test_query_includes_tstamp_filter_when_last_update_given(self):
        """When last_update is provided, query must include _tstamp $gt filter."""
        session = make_session()

        _run(consume(session.list_instances(user_id=42, last_update=1000.0)))

        query = session.collection.find.call_args[0][0]
        assert '_tstamp' in query
        assert query['_tstamp'] == {'$gt': 1000.0}

    def test_query_omits_tstamp_filter_when_no_last_update(self):
        """When last_update is None, query must not include a _tstamp filter."""
        session = make_session()

        _run(consume(session.list_instances(user_id=42, last_update=None)))

        query = session.collection.find.call_args[0][0]
        assert '_tstamp' not in query

    def test_last_update_value_is_passed_through_correctly(self):
        """The exact last_update value must appear in the $gt filter."""
        session = make_session()
        ts = 9876.543

        _run(consume(session.list_instances(user_id=42, last_update=ts)))

        query = session.collection.find.call_args[0][0]
        assert query['_tstamp'] == {'$gt': ts}
