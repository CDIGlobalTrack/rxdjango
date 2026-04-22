"""Reactive state for ContextChannel.

This module provides the :func:`reactive` field factory and supporting
machinery so that subclasses of :class:`rxdjango.channels.ContextChannel`
can declare reactive fields that are automatically synchronized with
connected clients over the WebSocket.

Usage::

    from rxdjango.channels import ContextChannel
    from rxdjango.state import reactive

    class ChatChannel(ContextChannel):

        notifications: int = reactive(default=0)
        mode: str = reactive(default='view')
        typing_users: list[int] = reactive(default_factory=list)

        async def some_handler(self, event):
            self.notifications += 1                  # broadcasts
            self.typing_users.append(event['uid'])   # broadcasts (auto-proxy)

            async with self.batch():
                self.mode = 'edit'
                self.notifications = 0
            # single websocket message with both changes

Design decisions pinned in ``Runtime-Refactor-FDD.md``:

* Reactive state is per-connection; it does not persist across reconnects.
* Writes must originate from an async context. Writing from a sync context
  (no running event loop) raises :class:`RuntimeError`.
* ``batch()`` is transactional: on exception the buffered writes are
  dropped, no broadcast is emitted, a warning is logged, the exception
  propagates.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any


logger = logging.getLogger(__name__)


class _Missing:
    """Sentinel for "no default provided"."""

    _instance: '_Missing | None' = None

    def __new__(cls) -> '_Missing':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return 'MISSING'

    def __bool__(self) -> bool:
        return False


MISSING: Any = _Missing()


def reactive(default: Any = MISSING,
             default_factory: Any = MISSING) -> 'ReactiveField':
    """Declare a reactive field on a :class:`ContextChannel` subclass.

    Reactive fields are read and written as ordinary attributes. Writes
    are synchronized to all connected clients over the WebSocket as
    ``runtimeVar`` (single write) or ``runtimeVars`` (batched) messages.

    Parameters
    ----------
    default
        Default value for the field. Used for immutables (ints, strings,
        tuples, frozen containers).
    default_factory
        Zero-argument callable that returns the default value. Used for
        mutables (lists, dicts, sets) to avoid shared state between
        channel instances.

    Exactly one of ``default`` / ``default_factory`` may be provided.
    Providing neither is allowed; the field will raise
    :class:`AttributeError` on read until the first write.

    Returns
    -------
    ReactiveField
        A descriptor that should be assigned at class body level.
    """
    if default is not MISSING and default_factory is not MISSING:
        raise TypeError(
            "reactive() accepts at most one of 'default' or 'default_factory'"
        )
    return ReactiveField(default=default, default_factory=default_factory)


class ReactiveField:
    """Descriptor that intercepts reads/writes of a reactive field.

    Do not instantiate directly; use :func:`reactive`.
    """

    __slots__ = ('name', 'default', 'default_factory', 'annotation')

    def __init__(self, default: Any = MISSING,
                 default_factory: Any = MISSING) -> None:
        self.name: str = ''
        self.default = default
        self.default_factory = default_factory
        self.annotation: Any = Any

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def initial_value(self) -> Any:
        """Return the initial value for a new channel instance."""
        if self.default_factory is not MISSING:
            return self.default_factory()
        if self.default is not MISSING:
            return self.default
        return MISSING

    # Descriptor protocol ---------------------------------------------------

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return self

        batch_stack = getattr(instance, '_batch_stack', None)
        store = instance.__dict__

        # Inside a batch, a pending write takes precedence over the
        # stored value. Containers are copied into the top frame on
        # first access so mutations don't leak into the committed
        # state until the batch exits successfully.
        if batch_stack:
            # Find a pending value in any active frame (innermost wins)
            for frame in reversed(batch_stack):
                if self.name in frame:
                    return self._wrap_container(instance, frame[self.name])

            # No pending value yet — if the stored value is a mutable
            # container, clone it into the top frame so further
            # in-place mutations are transactional.
            if self.name in store:
                value = store[self.name]
                if isinstance(value, (list, dict, set)):
                    clone = type(value)(value)
                    batch_stack[-1][self.name] = clone
                    return self._wrap_container(instance, clone)
                return self._wrap_container(instance, value)

            raise AttributeError(
                f"Reactive field {self.name!r} has no value yet and no "
                f"default. Either set a default / default_factory in "
                f"reactive() or assign a value before reading."
            )

        if self.name not in store:
            raise AttributeError(
                f"Reactive field {self.name!r} has no value yet and no "
                f"default. Either set a default / default_factory in "
                f"reactive() or assign a value before reading."
            )
        return self._wrap_container(instance, store[self.name])

    def __set__(self, instance: Any, value: Any) -> None:
        # Unwrap reactive containers before storing, so we never end
        # up with proxies-of-proxies inside buffers or on instance.
        value = _unwrap(value)

        batch_stack = getattr(instance, '_batch_stack', None)
        if batch_stack:
            # Inside a batch — buffer the pending write. No equality
            # check vs stored value here; the outer batch handles
            # net-zero deduplication on commit.
            batch_stack[-1][self.name] = value
            return

        # Normal write path — equality check against stored value
        current = instance.__dict__.get(self.name, MISSING)
        if current is not MISSING and _equal(current, value):
            return

        # Reactive writes must originate from an async context. Fail
        # loudly rather than queuing or silently dropping.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise RuntimeError(
                f"Cannot set reactive field {self.name!r} outside an "
                f"async context. Reactive writes must originate from "
                f"a channel handler (@consumer, @action, or a channel "
                f"lifecycle method)."
            )

        instance.__dict__[self.name] = value
        loop.create_task(instance._broadcast_field(self.name, value))

    # Helpers ---------------------------------------------------------------

    def _wrap_container(self, instance: Any, value: Any) -> Any:
        """Wrap lists/dicts/sets in reactive proxies on read.

        Caching behaviour: outside a batch, we cache the proxy back
        into the instance dict so subsequent reads return the same
        object and mutation methods stay bound to it. Inside a batch,
        we cache into the top batch frame — the stored instance value
        is untouched until the batch commits.
        """
        if isinstance(value, (ReactiveList, ReactiveDict, ReactiveSet)):
            return value

        batch_stack = getattr(instance, '_batch_stack', None)
        target: dict[str, Any]
        if batch_stack and self.name in batch_stack[-1]:
            target = batch_stack[-1]
        else:
            target = instance.__dict__

        if isinstance(value, list):
            wrapped = ReactiveList(value, instance, self.name)
        elif isinstance(value, dict):
            wrapped = ReactiveDict(value, instance, self.name)
        elif isinstance(value, set):
            wrapped = ReactiveSet(value, instance, self.name)
        else:
            return value

        target[self.name] = wrapped
        return wrapped


def _equal(a: Any, b: Any) -> bool:
    """Equality check that treats reactive proxies as their underlying type.

    Python's list/dict/set ``==`` already compares by value against our
    subclasses, so this is mostly a thin wrapper. We keep it in one
    place so future tweaks (e.g. special-casing NaN) live here.
    """
    try:
        return a == b
    except Exception:  # pragma: no cover - defensive
        return False


def _unwrap(value: Any) -> Any:
    """Return the underlying plain container for a reactive proxy.

    Proxies are transient views bound to a specific owner/field. When a
    value is reassigned between fields or instances, we strip the proxy
    so the new owner can wrap it fresh on next read.
    """
    if isinstance(value, ReactiveList):
        return list(value)
    if isinstance(value, ReactiveDict):
        return dict(value)
    if isinstance(value, ReactiveSet):
        return set(value)
    return value


# ---------------------------------------------------------------------------
# Reactive container proxies
# ---------------------------------------------------------------------------


def _notify(owner: Any, field_name: str, container: Any) -> None:
    """Notify the owning channel that a reactive container mutated.

    Outside a batch: schedule a broadcast with a snapshot of the
    container (so later mutations don't retroactively change the
    broadcast payload).

    Inside a batch: the container IS the batch buffer value (cloned
    into the buffer on first read). The mutation already updated it
    in place; commit will pick it up on batch exit. No action needed
    here — but we do verify the buffer still references this proxy.
    """
    batch_stack = getattr(owner, '_batch_stack', None)
    if batch_stack:
        # Make sure the top frame has a reference to the proxy so
        # commit sees the mutated value. If it doesn't (e.g. because
        # the mutation happened before any batch read of this field),
        # record it now.
        if field_name not in batch_stack[-1]:
            batch_stack[-1][field_name] = container
        return

    snapshot = _unwrap(container)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        raise RuntimeError(
            f"Cannot mutate reactive container {field_name!r} outside an "
            f"async context. Reactive writes must originate from a channel "
            f"handler (@consumer, @action, or a channel lifecycle method)."
        )

    loop.create_task(owner._broadcast_field(field_name, snapshot))


class ReactiveList(list):
    """A ``list`` that broadcasts when mutated in place."""

    __slots__ = ('_rx_owner', '_rx_field')

    def __init__(self, initial: Any, owner: Any, field_name: str) -> None:
        super().__init__(initial)
        self._rx_owner = owner
        self._rx_field = field_name

    # Mutators
    def append(self, value: Any) -> None:
        super().append(value)
        _notify(self._rx_owner, self._rx_field, self)

    def extend(self, iterable: Any) -> None:
        super().extend(iterable)
        _notify(self._rx_owner, self._rx_field, self)

    def insert(self, index: int, value: Any) -> None:
        super().insert(index, value)
        _notify(self._rx_owner, self._rx_field, self)

    def remove(self, value: Any) -> None:
        super().remove(value)
        _notify(self._rx_owner, self._rx_field, self)

    def pop(self, index: int = -1) -> Any:
        result = super().pop(index)
        _notify(self._rx_owner, self._rx_field, self)
        return result

    def clear(self) -> None:
        if not len(self):
            return
        super().clear()
        _notify(self._rx_owner, self._rx_field, self)

    def sort(self, *args: Any, **kwargs: Any) -> None:
        super().sort(*args, **kwargs)
        _notify(self._rx_owner, self._rx_field, self)

    def reverse(self) -> None:
        super().reverse()
        _notify(self._rx_owner, self._rx_field, self)

    def __setitem__(self, index: Any, value: Any) -> None:
        super().__setitem__(index, value)
        _notify(self._rx_owner, self._rx_field, self)

    def __delitem__(self, index: Any) -> None:
        super().__delitem__(index)
        _notify(self._rx_owner, self._rx_field, self)

    def __iadd__(self, other: Any) -> 'ReactiveList':
        result = super().__iadd__(other)
        _notify(self._rx_owner, self._rx_field, self)
        return result

    def __imul__(self, n: int) -> 'ReactiveList':
        result = super().__imul__(n)
        _notify(self._rx_owner, self._rx_field, self)
        return result


class ReactiveDict(dict):
    """A ``dict`` that broadcasts when mutated in place."""

    __slots__ = ('_rx_owner', '_rx_field')

    def __init__(self, initial: Any, owner: Any, field_name: str) -> None:
        super().__init__(initial)
        self._rx_owner = owner
        self._rx_field = field_name

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(key, value)
        _notify(self._rx_owner, self._rx_field, self)

    def __delitem__(self, key: Any) -> None:
        super().__delitem__(key)
        _notify(self._rx_owner, self._rx_field, self)

    def update(self, *args: Any, **kwargs: Any) -> None:
        super().update(*args, **kwargs)
        _notify(self._rx_owner, self._rx_field, self)

    def setdefault(self, key: Any, default: Any = None) -> Any:
        had_key = key in self
        result = super().setdefault(key, default)
        if not had_key:
            _notify(self._rx_owner, self._rx_field, self)
        return result

    def pop(self, key: Any, *args: Any) -> Any:
        had_key = key in self
        result = super().pop(key, *args)
        if had_key:
            _notify(self._rx_owner, self._rx_field, self)
        return result

    def popitem(self) -> Any:
        result = super().popitem()
        _notify(self._rx_owner, self._rx_field, self)
        return result

    def clear(self) -> None:
        if not len(self):
            return
        super().clear()
        _notify(self._rx_owner, self._rx_field, self)


class ReactiveSet(set):
    """A ``set`` that broadcasts when mutated in place."""

    __slots__ = ('_rx_owner', '_rx_field')

    def __init__(self, initial: Any, owner: Any, field_name: str) -> None:
        super().__init__(initial)
        self._rx_owner = owner
        self._rx_field = field_name

    def add(self, value: Any) -> None:
        had = value in self
        super().add(value)
        if not had:
            _notify(self._rx_owner, self._rx_field, self)

    def discard(self, value: Any) -> None:
        had = value in self
        super().discard(value)
        if had:
            _notify(self._rx_owner, self._rx_field, self)

    def remove(self, value: Any) -> None:
        super().remove(value)
        _notify(self._rx_owner, self._rx_field, self)

    def pop(self) -> Any:
        result = super().pop()
        _notify(self._rx_owner, self._rx_field, self)
        return result

    def clear(self) -> None:
        if not len(self):
            return
        super().clear()
        _notify(self._rx_owner, self._rx_field, self)

    def update(self, *others: Any) -> None:
        before = len(self)
        super().update(*others)
        if len(self) != before:
            _notify(self._rx_owner, self._rx_field, self)

    def intersection_update(self, *others: Any) -> None:
        before = frozenset(self)
        super().intersection_update(*others)
        if frozenset(self) != before:
            _notify(self._rx_owner, self._rx_field, self)

    def difference_update(self, *others: Any) -> None:
        before = frozenset(self)
        super().difference_update(*others)
        if frozenset(self) != before:
            _notify(self._rx_owner, self._rx_field, self)

    def symmetric_difference_update(self, other: Any) -> None:
        before = frozenset(self)
        super().symmetric_difference_update(other)
        if frozenset(self) != before:
            _notify(self._rx_owner, self._rx_field, self)


# ---------------------------------------------------------------------------
# Batch context
# ---------------------------------------------------------------------------


class BatchContext:
    """Async context manager for atomic multi-field reactive updates.

    Usage::

        async with channel.batch():
            channel.foo = 1
            channel.bar = 2
        # one websocket message with both changes

    Semantics:

    * On enter, push an empty dict onto ``channel._batch_stack``.
    * Reactive writes inside the block are buffered on the top frame
      instead of being broadcast individually.
    * Reactive reads inside the block see pending values first.
    * On successful exit of the outermost batch, buffered writes commit
      to the instance and a single ``runtimeVars`` message is sent.
      Fields whose net change equals the pre-batch value are omitted.
    * On exception, the buffer is discarded — values remain at their
      pre-batch state, no broadcast is emitted, a warning is logged,
      and the exception propagates.
    * Nested batches merge into the parent buffer on successful inner
      exit; inner failures discard only the inner frame.
    """

    __slots__ = ('_channel',)

    def __init__(self, channel: Any) -> None:
        self._channel = channel

    async def __aenter__(self) -> 'BatchContext':
        if not hasattr(self._channel, '_batch_stack'):
            self._channel._batch_stack = []
        self._channel._batch_stack.append({})
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any,
                        exc_tb: Any) -> None:
        stack = self._channel._batch_stack
        frame = stack.pop()

        if exc_type is not None:
            # Discard this frame entirely. If it was the outermost,
            # nothing committed. If we're nested, the parent frame
            # is untouched so its pending writes are preserved.
            if frame:
                logger.warning(
                    "Discarding %d buffered reactive write(s) on %s "
                    "due to exception in batch(): %s",
                    len(frame),
                    type(self._channel).__name__,
                    sorted(frame.keys()),
                )
            return

        if not frame:
            return

        if stack:
            # Nested batch — merge into the parent frame.
            stack[-1].update(frame)
            return

        # Outermost batch — commit to instance and broadcast.
        changed: dict[str, Any] = {}
        for name, value in frame.items():
            current = self._channel.__dict__.get(name, MISSING)
            # Unwrap proxies before comparing and storing so the
            # committed value is a plain container.
            plain = _unwrap(value)
            if current is not MISSING and _equal(current, plain):
                continue
            self._channel.__dict__[name] = plain
            changed[name] = plain

        if not changed:
            return

        await self._channel._broadcast_fields(changed)


__all__ = [
    'reactive',
    'ReactiveField',
    'ReactiveList',
    'ReactiveDict',
    'ReactiveSet',
    'BatchContext',
    'MISSING',
]
