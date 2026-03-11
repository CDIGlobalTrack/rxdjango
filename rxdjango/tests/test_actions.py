"""
Tests for rxdjango.actions module.

Tests the @action decorator, method registration, type introspection,
and parameter conversion.
"""
import asyncio
from datetime import datetime

import pytest

from rxdjango.actions import action, list_actions, execute_action, _verify_method
from rxdjango.exceptions import ForbiddenError, ActionNotAsync


def _run(coro):
    """Run an async coroutine in a fresh event loop."""
    return asyncio.run(coro)


class TestActionDecorator:
    """Tests for the @action decorator."""

    def test_async_method_accepted(self):
        """@action should accept async methods."""
        @action
        async def my_action(self, param: str):
            pass

        # Should return the method unchanged
        assert asyncio.iscoroutinefunction(my_action)

    def test_sync_method_raises(self):
        """@action should reject non-async methods."""
        with pytest.raises(ActionNotAsync, match='requires.*to be async'):
            @action
            def my_sync_action(self, param: str):
                pass

    def test_datetime_fields_detected(self):
        """@action should detect datetime type hints for auto-conversion."""
        @action
        async def with_datetime(self, name: str, created: datetime, count: int):
            pass

        # 'created' is at index 1 (after 'name', skipping 'self')
        assert 1 in getattr(with_datetime, '__datetime_fields')

    def test_no_datetime_fields(self):
        """Methods without datetime params should have empty __datetime_fields."""
        @action
        async def no_dates(self, name: str, count: int):
            pass

        assert getattr(no_dates, '__datetime_fields') == []

    def test_multiple_datetime_fields(self):
        """Multiple datetime params should all be tracked."""
        @action
        async def multi_dates(self, start: datetime, end: datetime):
            pass

        assert 0 in getattr(multi_dates, '__datetime_fields')
        assert 1 in getattr(multi_dates, '__datetime_fields')

    def test_return_type_excluded(self):
        """Return type hint should not be included in datetime detection."""
        @action
        async def returns_dt(self, name: str) -> datetime:
            pass

        assert getattr(returns_dt, '__datetime_fields') == []


class TestVerifyMethod:
    """Tests for _verify_method security check."""

    def test_none_method_raises_forbidden(self):
        """Passing None should raise ForbiddenError."""
        with pytest.raises(ForbiddenError):
            _verify_method(None)

    def test_unregistered_method_raises_forbidden(self):
        """Non-action method should raise ForbiddenError."""
        async def not_an_action(self):
            pass

        with pytest.raises(ForbiddenError):
            _verify_method(not_an_action)

    def test_registered_method_passes(self):
        """Registered action method should pass verification."""
        @action
        async def valid_action(self):
            pass

        # Should not raise
        _verify_method(valid_action)


class TestListActions:
    """Tests for list_actions function."""

    def test_list_actions_finds_decorated(self):
        """list_actions should yield methods decorated with @action."""
        @action
        async def my_action(self):
            pass

        class FakeChannel:
            action_method = my_action

        actions = list(list_actions(FakeChannel))
        assert my_action in actions

    def test_list_actions_skips_non_action(self):
        """list_actions should skip non-action attributes."""
        class FakeChannel:
            name = "test"
            count = 42

            async def regular_method(self):
                pass

        actions = list(list_actions(FakeChannel))
        assert len(actions) == 0


class TestExecuteAction:
    """Tests for execute_action function."""

    def test_execute_calls_method(self):
        """execute_action should call the correct method with params."""
        @action
        async def do_something(self, name: str, count: int):
            return {'name': name, 'count': count}

        channel = type('FakeChannel', (), {'do_something': do_something})()
        result = _run(execute_action(channel, 'do_something', ['hello', 5]))
        assert result == {'name': 'hello', 'count': 5}

    def test_execute_converts_datetime_params(self):
        """execute_action should convert ISO string to datetime for datetime params."""
        @action
        async def set_date(self, when: datetime):
            return when

        channel = type('FakeChannel', (), {'set_date': set_date})()
        result = _run(execute_action(channel, 'set_date', ['2024-01-15T10:30:00']))
        assert isinstance(result, datetime)
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15

    def test_execute_nonexistent_method_raises_forbidden(self):
        """Calling a non-existent method should raise ForbiddenError."""
        class FakeChannel:
            pass

        channel = FakeChannel()
        with pytest.raises(ForbiddenError):
            _run(execute_action(channel, 'nonexistent', []))

    def test_execute_non_action_method_raises_forbidden(self):
        """Calling a method not decorated with @action should raise."""
        class FakeChannel:
            async def secret_method(self):
                pass

        channel = FakeChannel()
        with pytest.raises(ForbiddenError):
            _run(execute_action(channel, 'secret_method', []))
