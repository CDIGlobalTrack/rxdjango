"""Unit tests for rxdjango.state (reactive fields, containers, batching).

These tests exercise the reactive state machinery in isolation, without
any Django, Redis, Mongo, or real websocket dependencies. We use a
stub "channel" class that records broadcast calls.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from rxdjango.state import (
    BatchContext,
    MISSING,
    ReactiveField,
    reactive,
)


# ---------------------------------------------------------------------------
# Test fixtures — a minimal channel-like host for ReactiveField
# ---------------------------------------------------------------------------


class StubChannel:
    """Mimics the subset of ContextChannel that ReactiveField talks to."""

    def __init__(self) -> None:
        self._batch_stack: list[dict] = []
        self.single_broadcasts: list[tuple[str, Any]] = []
        self.batch_broadcasts: list[dict] = []

    async def _broadcast_field(self, name: str, value: Any) -> None:
        self.single_broadcasts.append((name, value))

    async def _broadcast_fields(self, fields: dict[str, Any]) -> None:
        self.batch_broadcasts.append(fields)

    def batch(self) -> BatchContext:
        return BatchContext(self)


def make_channel_with_fields(**fields: ReactiveField) -> type:
    """Build a StubChannel subclass that has the given reactive fields.

    Mirrors what ContextChannelMeta would do minus the Django machinery.
    """
    cls = type('ReactiveStub', (StubChannel,), dict(fields))
    reactive_fields = {}
    for name, value in fields.items():
        if isinstance(value, ReactiveField):
            # Simulate __set_name__ / metaclass annotation capture
            value.__set_name__(cls, name)
            reactive_fields[name] = value
    cls.__reactive_fields__ = reactive_fields
    return cls


async def drain() -> None:
    """Let scheduled broadcast tasks run to completion."""
    # One pass gives the event loop a chance to run create_task'd coros.
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Field declaration and defaults
# ---------------------------------------------------------------------------


class TestReactiveFactory:
    def test_default_value(self) -> None:
        Cls = make_channel_with_fields(n=reactive(default=0))
        ch = Cls()
        # Populate initial value as ContextChannel.__init__ would
        ch.__dict__['n'] = Cls.__reactive_fields__['n'].initial_value()
        assert ch.n == 0

    def test_default_factory_returns_new_instance(self) -> None:
        Cls = make_channel_with_fields(items=reactive(default_factory=list))
        a = Cls()
        b = Cls()
        a.__dict__['items'] = Cls.__reactive_fields__['items'].initial_value()
        b.__dict__['items'] = Cls.__reactive_fields__['items'].initial_value()
        assert a.items == []
        assert b.items == []
        assert a.items is not b.items, "default_factory must not share state"

    def test_both_default_and_factory_raises(self) -> None:
        with pytest.raises(TypeError, match="at most one of"):
            reactive(default=[], default_factory=list)

    def test_neither_default_nor_factory_raises_on_read(self) -> None:
        Cls = make_channel_with_fields(x=reactive())
        ch = Cls()
        # No initial value assigned
        with pytest.raises(AttributeError, match="no value yet"):
            _ = ch.x

    def test_initial_value_missing_when_no_default(self) -> None:
        field = reactive()
        assert field.initial_value() is MISSING


# ---------------------------------------------------------------------------
# Assignment broadcasts
# ---------------------------------------------------------------------------


class TestAssignment:
    def setup_method(self) -> None:
        self.Cls = make_channel_with_fields(
            n=reactive(default=0),
            s=reactive(default='view'),
        )

    def _fresh(self) -> StubChannel:
        ch = self.Cls()
        ch.__dict__['n'] = 0
        ch.__dict__['s'] = 'view'
        return ch

    def test_set_broadcasts_once(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            ch.n = 5
            await drain()
            assert ch.single_broadcasts == [('n', 5)]
            assert ch.batch_broadcasts == []
        asyncio.run(go())

    def test_same_value_does_not_broadcast(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            ch.n = 0  # same as default
            await drain()
            assert ch.single_broadcasts == []
        asyncio.run(go())

    def test_undeclared_attribute_is_plain(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            ch.not_reactive = 99
            await drain()
            assert ch.single_broadcasts == []
            assert ch.not_reactive == 99
        asyncio.run(go())

    def test_sync_context_raises(self) -> None:
        ch = self._fresh()
        # No running loop here
        with pytest.raises(RuntimeError, match="outside an async context"):
            ch.n = 7

    def test_write_then_read_returns_new_value(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            ch.n = 42
            await drain()
            assert ch.n == 42
        asyncio.run(go())


# ---------------------------------------------------------------------------
# Reactive list
# ---------------------------------------------------------------------------


class TestReactiveList:
    def setup_method(self) -> None:
        self.Cls = make_channel_with_fields(
            items=reactive(default_factory=list),
        )

    def _fresh(self) -> StubChannel:
        ch = self.Cls()
        ch.__dict__['items'] = []
        return ch

    def test_append_broadcasts(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            ch.items.append(1)
            await drain()
            assert ch.single_broadcasts == [('items', [1])]
        asyncio.run(go())

    def test_extend_broadcasts_once(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            ch.items.extend([1, 2, 3])
            await drain()
            assert ch.single_broadcasts == [('items', [1, 2, 3])]
        asyncio.run(go())

    def test_clear_broadcasts_when_non_empty(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            ch.items.extend([1, 2])
            await drain()
            ch.single_broadcasts.clear()
            ch.items.clear()
            await drain()
            assert ch.single_broadcasts == [('items', [])]
        asyncio.run(go())

    def test_clear_on_empty_skips(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            ch.items.clear()
            await drain()
            assert ch.single_broadcasts == []
        asyncio.run(go())

    def test_multiple_ops_each_broadcast(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            ch.items.append(1)
            ch.items.append(2)
            ch.items.pop()
            await drain()
            assert ch.single_broadcasts == [
                ('items', [1]),
                ('items', [1, 2]),
                ('items', [1]),
            ]
        asyncio.run(go())

    def test_read_does_not_broadcast(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            ch.items.append(1)
            await drain()
            ch.single_broadcasts.clear()
            _ = list(ch.items)  # pure read
            _ = len(ch.items)
            await drain()
            assert ch.single_broadcasts == []
        asyncio.run(go())

    def test_assigned_plain_list_then_mutation_broadcasts(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            ch.items = [1, 2]  # fresh assignment
            await drain()
            ch.single_broadcasts.clear()
            ch.items.append(3)  # proxy rewraps on access
            await drain()
            assert ch.single_broadcasts == [('items', [1, 2, 3])]
        asyncio.run(go())

    def test_setitem_broadcasts(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            ch.items.extend([1, 2, 3])
            await drain()
            ch.single_broadcasts.clear()
            ch.items[0] = 99
            await drain()
            assert ch.single_broadcasts == [('items', [99, 2, 3])]
        asyncio.run(go())


# ---------------------------------------------------------------------------
# Reactive dict
# ---------------------------------------------------------------------------


class TestReactiveDict:
    def setup_method(self) -> None:
        self.Cls = make_channel_with_fields(
            mapping=reactive(default_factory=dict),
        )

    def _fresh(self) -> StubChannel:
        ch = self.Cls()
        ch.__dict__['mapping'] = {}
        return ch

    def test_setitem_broadcasts(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            ch.mapping['k'] = 'v'
            await drain()
            assert ch.single_broadcasts == [('mapping', {'k': 'v'})]
        asyncio.run(go())

    def test_pop_broadcasts_when_key_exists(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            ch.mapping['k'] = 'v'
            await drain()
            ch.single_broadcasts.clear()
            ch.mapping.pop('k')
            await drain()
            assert ch.single_broadcasts == [('mapping', {})]
        asyncio.run(go())

    def test_pop_missing_key_with_default_skips(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            ch.mapping.pop('nope', None)
            await drain()
            assert ch.single_broadcasts == []
        asyncio.run(go())

    def test_update_broadcasts(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            ch.mapping.update({'a': 1, 'b': 2})
            await drain()
            assert ch.single_broadcasts == [('mapping', {'a': 1, 'b': 2})]
        asyncio.run(go())


# ---------------------------------------------------------------------------
# Reactive set
# ---------------------------------------------------------------------------


class TestReactiveSet:
    def setup_method(self) -> None:
        self.Cls = make_channel_with_fields(
            tags=reactive(default_factory=set),
        )

    def _fresh(self) -> StubChannel:
        ch = self.Cls()
        ch.__dict__['tags'] = set()
        return ch

    def test_add_broadcasts(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            ch.tags.add('a')
            await drain()
            assert ch.single_broadcasts == [('tags', {'a'})]
        asyncio.run(go())

    def test_add_existing_skips(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            ch.tags.add('a')
            await drain()
            ch.single_broadcasts.clear()
            ch.tags.add('a')
            await drain()
            assert ch.single_broadcasts == []
        asyncio.run(go())

    def test_discard_missing_skips(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            ch.tags.discard('nope')
            await drain()
            assert ch.single_broadcasts == []
        asyncio.run(go())


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------


class TestBatching:
    def setup_method(self) -> None:
        self.Cls = make_channel_with_fields(
            n=reactive(default=0),
            s=reactive(default='view'),
            items=reactive(default_factory=list),
        )

    def _fresh(self) -> StubChannel:
        ch = self.Cls()
        ch.__dict__['n'] = 0
        ch.__dict__['s'] = 'view'
        ch.__dict__['items'] = []
        return ch

    def test_batch_emits_single_message(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            async with ch.batch():
                ch.n = 5
                ch.s = 'edit'
            assert ch.single_broadcasts == []
            assert ch.batch_broadcasts == [{'n': 5, 's': 'edit'}]
            assert ch.n == 5
            assert ch.s == 'edit'
        asyncio.run(go())

    def test_multiple_writes_same_field_keep_final(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            async with ch.batch():
                ch.n = 1
                ch.n = 2
                ch.n = 3
            assert ch.batch_broadcasts == [{'n': 3}]
        asyncio.run(go())

    def test_net_zero_field_is_omitted(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            async with ch.batch():
                ch.n = 99
                ch.n = 0  # back to default
                ch.s = 'edit'
            assert ch.batch_broadcasts == [{'s': 'edit'}]
        asyncio.run(go())

    def test_empty_batch_sends_nothing(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            async with ch.batch():
                pass
            assert ch.single_broadcasts == []
            assert ch.batch_broadcasts == []
        asyncio.run(go())

    def test_batch_read_sees_pending(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            async with ch.batch():
                ch.n = 42
                assert ch.n == 42, "read inside batch must see pending value"
            assert ch.n == 42
        asyncio.run(go())

    def test_nested_batch_merges(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            async with ch.batch():
                ch.n = 1
                async with ch.batch():
                    ch.s = 'edit'
                # Still no broadcast yet — outer batch pending
                assert ch.batch_broadcasts == []
            assert ch.batch_broadcasts == [{'n': 1, 's': 'edit'}]
        asyncio.run(go())

    def test_exception_discards_buffer(self, caplog: Any) -> None:
        async def go() -> None:
            ch = self._fresh()
            with caplog.at_level(logging.WARNING, logger='rxdjango.state'):
                with pytest.raises(RuntimeError, match="boom"):
                    async with ch.batch():
                        ch.n = 99
                        ch.s = 'edit'
                        raise RuntimeError("boom")

            assert ch.single_broadcasts == []
            assert ch.batch_broadcasts == []
            assert ch.n == 0, "n must remain at pre-batch value"
            assert ch.s == 'view', "s must remain at pre-batch value"
            assert any('Discarding' in rec.message
                       for rec in caplog.records), \
                "A warning naming the discard must be logged"
        asyncio.run(go())

    def test_inner_exception_preserves_outer(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            async with ch.batch():
                ch.n = 1
                try:
                    async with ch.batch():
                        ch.s = 'edit'
                        raise RuntimeError("inner boom")
                except RuntimeError:
                    pass
                # Outer batch continues with its own pending writes
                ch.items = [10]
            # Inner frame was discarded; outer commits n and items
            assert ch.batch_broadcasts == [{'n': 1, 'items': [10]}]
            assert ch.s == 'view', "inner-only write was discarded"
        asyncio.run(go())

    def test_container_mutation_in_batch_buffers(self) -> None:
        async def go() -> None:
            ch = self._fresh()
            async with ch.batch():
                ch.items.append(1)
                ch.items.append(2)
            assert ch.single_broadcasts == []
            assert ch.batch_broadcasts == [{'items': [1, 2]}]
        asyncio.run(go())


# ---------------------------------------------------------------------------
# Inheritance
# ---------------------------------------------------------------------------


class TestInheritance:
    def test_subclass_inherits_fields(self) -> None:
        # Build base and derived manually, as metaclass would.
        base_n = reactive(default=0)
        Base = type('Base', (StubChannel,), {'n': base_n})
        base_n.__set_name__(Base, 'n')
        Base.__reactive_fields__ = {'n': base_n}

        child_s = reactive(default='hi')
        Child = type('Child', (Base,), {'s': child_s})
        child_s.__set_name__(Child, 's')
        Child.__reactive_fields__ = {'n': base_n, 's': child_s}

        ch = Child()
        ch.__dict__['n'] = 0
        ch.__dict__['s'] = 'hi'
        assert ch.n == 0
        assert ch.s == 'hi'


# ---------------------------------------------------------------------------
# Equality edge cases
# ---------------------------------------------------------------------------


class TestEquality:
    def test_equal_but_distinct_dict_skips(self) -> None:
        async def go() -> None:
            Cls = make_channel_with_fields(
                m=reactive(default_factory=dict),
            )
            ch = Cls()
            ch.__dict__['m'] = {'a': 1}
            ch.m = {'a': 1}  # equal but distinct object
            await drain()
            assert ch.single_broadcasts == []
        asyncio.run(go())

    def test_nan_always_broadcasts(self) -> None:
        async def go() -> None:
            Cls = make_channel_with_fields(x=reactive(default=float('nan')))
            ch = Cls()
            ch.__dict__['x'] = float('nan')
            ch.x = float('nan')  # NaN != NaN, so this broadcasts
            await drain()
            assert len(ch.single_broadcasts) == 1
        asyncio.run(go())
