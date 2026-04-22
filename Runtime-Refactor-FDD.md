# Feature Design Doc: Runtime State Refactor

**Target release:** v0.3.0
**Status:** Approved, ready to implement
**Compatibility:** Breaking change. No deprecation shims. No users other than the core team.

---

## 1. Motivation

The current `RuntimeState` API is not idiomatic Python. Users declare a `TypedDict` nested in their channel class, then read values with dict lookups and write them with a method call that takes a stringly-typed name:

```python
class MyChannel(ContextChannel):

    class RuntimeState(TypedDict):
        notifications: int

    @consumer('new.notification')
    async def relay(self, event):
        n = self.runtime_state['notifications']       # dict read
        await self.set_runtime_var('notifications', n + 1)   # string-keyed setter
```

Problems, in order of severity:

1. Read/write asymmetry — dict access for read, method call for write, same concept.
2. Stringly-typed writes — `'notifications'` is a string; typos are runtime errors and renames need grep.
3. `TypedDict` was chosen to satisfy the TypeScript generator, not to serve the Python developer.
4. Two names for one concept — `RuntimeState` (the class) and `runtime_state` (the dict).
5. `await` on every write, even though the user is just changing a value.
6. No place to declare defaults.
7. "Var" is C-brained. Python says *attribute*.

## 2. The new API

Reactive fields declared at the class level as descriptors, read and written as ordinary attributes. The descriptor schedules the broadcast.

```python
from rxdjango.channels import ContextChannel
from rxdjango.state import reactive

class ChatChannel(ContextChannel):

    notifications: int = reactive(default=0)
    mode: str = reactive(default='view')
    typing_users: list[int] = reactive(default_factory=list)
    metadata: dict[str, str] = reactive(default_factory=dict)

    @consumer('new.notification')
    async def relay_notification(self, event):
        self.notifications += 1                          # broadcasts
        self.typing_users.append(event['user_id'])       # broadcasts (auto-proxy)
        self.metadata['last_seen'] = event['timestamp']  # broadcasts (auto-proxy)

    @consumer('batch.update')
    async def bulk_change(self, event):
        async with self.batch():
            self.mode = 'edit'
            self.notifications = 0
            self.typing_users.clear()
        # single websocket message with all three changes
```

Key behaviours:

- **Attribute access** for both read and write. No dicts, no method calls, no `await` on writes.
- **Class-level type annotations** are the source of truth for both Python typing and the TypeScript generator.
- **Defaults** via `default=` or `default_factory=`, mirroring `dataclasses.field`.
- **Auto-proxy for mutable containers** — `list`, `dict`, `set` are wrapped so in-place mutation (`.append`, `[key] = v`, `.add`) broadcasts. Nested containers proxy lazily.
- **Explicit batching** via `async with self.batch():`. No tick coalescing.
- **Only declared reactive fields are reactive.** Plain `self.foo = 1` on an undeclared name stays a normal attribute.

## 3. Removed surface

All of the following are deleted with no deprecation layer:

- `ContextChannel.RuntimeState` nested class pattern.
- `ContextChannel.runtime_state` attribute (the dict).
- `ContextChannel.set_runtime_var(name, value)` method.

## 4. Wire protocol

Single-field update — **unchanged**:

```json
{"type": "runtimeVar", "var": "notifications", "value": 3}
```

Batched update — **new**:

```json
{"type": "runtimeVars", "vars": {"mode": "edit", "notifications": 0, "typing_users": []}}
```

## 5. Implementation

### 5.1 New module: `rxdjango/state.py`

Public:

- `reactive(default=MISSING, default_factory=MISSING) -> ReactiveField`
  Factory returning a `ReactiveField` descriptor. Exactly one of `default` / `default_factory` may be provided; both omitted is allowed only if the field is guaranteed to be set before read — otherwise reads raise `AttributeError`.

Internal:

- `class ReactiveField`
  Descriptor. `__set_name__` captures the attribute name. `__get__` returns the stored value, wrapping lists/dicts/sets in the proxy types below on first access (cached); if a batch is active on the channel and this field has a pending write in the top buffer, `__get__` returns the pending value instead. `__set__` performs an equality check; if a batch is active, writes the new value into the top buffer and returns without broadcasting; otherwise stores the value on the instance and schedules a broadcast task via `asyncio.get_running_loop().create_task(...)`. If there is no running loop, `__set__` raises `RuntimeError` with a message naming the field and explaining that reactive writes must originate from an async channel handler (`@consumer`, `@action`, or a lifecycle method). No queueing, no silent fallback.

- `class ReactiveList(list)`, `class ReactiveDict(dict)`, `class ReactiveSet(set)`
  Override every mutating method (`append`, `extend`, `insert`, `remove`, `pop`, `clear`, `sort`, `reverse`, `__setitem__`, `__delitem__`, `__iadd__`, `__imul__`; `update`, `setdefault`, `popitem`; `add`, `discard`, `update`, `intersection_update`, `difference_update`, `symmetric_difference_update`). Each override calls super, then notifies the owning channel with the full new container value. Nested containers returned from `__getitem__` are wrapped lazily.

- `class BatchContext`
  Async context manager with **transactional semantics**. On enter, pushes an empty dict onto `channel._batch_stack`. While active, reactive `__set__` writes into the top buffer instead of the instance, and reactive `__get__` reads from the top buffer if the field has a pending value there. On successful exit of the outermost batch, applies every pending write to the instance's stored values and emits one `runtimeVars` message containing all changes (after final-value dedup against pre-batch values; fields whose net change is zero are omitted). On exception, the buffer is discarded — instance values are unchanged, no broadcast is emitted, the exception propagates, and a `logging.warning` is logged naming the channel class and the discarded field names. Nested `async with channel.batch():` calls push additional buffers that merge into the parent on successful inner exit; inner failures discard only the inner buffer.

- Sentinel: `MISSING`.

### 5.2 Changes to `rxdjango/channels.py`

In `ContextChannelMeta.__new__`, after the existing setup, collect reactive fields:

```python
reactive_fields = {}
for klass in reversed(cls.__mro__):
    for name, value in vars(klass).items():
        if isinstance(value, ReactiveField):
            reactive_fields[name] = value
            value.annotation = klass.__annotations__.get(name, Any)
cls.__reactive_fields__ = reactive_fields
```

In `ContextChannel.__init__`, replace:

```python
self.runtime_state = self.RuntimeState() if self.RuntimeState else None
```

with per-field initialization from `default` or `default_factory`. Also initialize `self._batch_stack: list[dict] = []`.

Delete:

- `RuntimeState = None` class attribute.
- `set_runtime_var` method.

Add:

- `def batch(self) -> BatchContext` — returns a `BatchContext` bound to this channel.
- `async def _broadcast_field(self, name, value)` — private, sends the `runtimeVar` message.
- `async def _broadcast_fields(self, fields: dict)` — private, sends the `runtimeVars` message.

### 5.3 Changes to `rxdjango/ts/channels.py`

Replace the block around line 524:

```python
if getattr(context_channel_class, 'RuntimeState', False):
    runtime_type = f'{context_channel_class.__name__}RuntimeState'
    types = f'{state_type}, {runtime_type}'
else:
    runtime_type = None
    types = state_type
```

with:

```python
if context_channel_class.__reactive_fields__:
    runtime_type = f'{context_channel_class.__name__}RuntimeState'
    types = f'{state_type}, {runtime_type}'
else:
    runtime_type = None
    types = state_type
```

And at line 552, replace:

```python
types = typing.get_type_hints(context_channel_class.RuntimeState)
```

with:

```python
types = {name: f.annotation
         for name, f in context_channel_class.__reactive_fields__.items()}
```

The generated TypeScript interface must remain byte-for-byte identical to the current output for the same set of field names and types. This is enforced by a test (see 6.3).

### 5.4 Changes to `rxdjango-react/src/PersistentWebsocket.ts`

In the message routing `switch` (around line 148), add a case for `runtimeVars`:

```ts
case 'runtimeVars':
  this.onRuntimeStateChange(message);
  break;
```

### 5.5 Changes to `rxdjango-react/src/ContextChannel.ts`

In `ws.onRuntimeStateChange` (around line 101), handle both message shapes:

```ts
ws.onRuntimeStateChange = (message) => {
  const msg = message as
    | { type: 'runtimeVar'; var: keyof Y; value: unknown }
    | { type: 'runtimeVars'; vars: Partial<Y> };
  if (msg.type === 'runtimeVar') {
    this.receiveRuntimeState(msg.var, msg.value);
  } else {
    this.receiveRuntimeStateBatch(msg.vars);
  }
};
```

Add a `receiveRuntimeStateBatch(vars)` method that does a single merge-and-notify, so batched updates produce a single React re-render.

## 6. Testing

Three tiers. All must pass before merge.

### 6.1 Unit tests — `rxdjango/tests/test_reactive.py` (new)

Runs under plain `pytest rxdjango/tests/`. No Django settings, no Redis, no Mongo. Uses a dummy channel class with a mocked `_consumer.send`.

Required test cases:

**Field declaration and access:**
- `reactive(default=0)` stores and returns `0` on fresh instance.
- `reactive(default_factory=list)` returns a new list per instance (no shared state between instances).
- Providing both `default` and `default_factory` raises `TypeError`.
- Providing neither raises `AttributeError` on first read.
- `__reactive_fields__` dict is populated on the class with correct names and annotations.
- Inheritance — reactive fields from a parent channel class are inherited.

**Assignment broadcasts:**
- `channel.notifications = 5` calls `_broadcast_field('notifications', 5)` exactly once.
- Assigning the same value (equality) does NOT broadcast.
- Assigning to an undeclared attribute does NOT broadcast.

**Container auto-proxy:**
- `channel.items.append(x)` broadcasts the full new list.
- `channel.items.extend([a, b])` broadcasts once with final list.
- `channel.items.clear()` broadcasts `[]`.
- `channel.mapping['k'] = v` broadcasts the full new dict.
- `channel.mapping.pop('k')` broadcasts.
- `channel.tags.add('x')` / `.discard('x')` broadcast.
- Nested container mutation (`channel.mapping['users'].append(x)` where value is a list) broadcasts.
- Assigning a plain `list` to a reactive list field — subsequent in-place mutations still broadcast (proxy rewraps).
- Reading a container does NOT broadcast.

**Batching:**
- Inside `async with channel.batch():`, no individual `runtimeVar` messages are sent.
- On exit, one `runtimeVars` message is sent containing all changed fields.
- If a field is assigned multiple times in a batch, only the final value ships.
- If a field is assigned its original value in a batch (net no-op), it is omitted from the batch message.
- Nested `async with channel.batch():` collapses — only the outermost emits.
- Empty batch (no writes) emits nothing.
- Exception inside batch — buffer is discarded. Instance values remain at their pre-batch state. No websocket message is sent. The exception propagates. A warning is logged.
- Inside a batch, reading a field that was just assigned in the same batch returns the pending (buffered) value, not the pre-batch value.
- Assigning a reactive field from a sync context with no running event loop raises `RuntimeError` naming the field. No broadcast is queued or dropped silently.

**Equality edge cases:**
- Assigning `NaN` (which is not equal to itself) broadcasts every time — documented behavior.
- Assigning an equal-but-not-identical dict (`{'a': 1}` to existing `{'a': 1}`) does NOT broadcast.

### 6.2 Unit tests — `rxdjango/tests/test_ts_reactive_gen.py` (new)

Validates the TypeScript generator.

- Channel with reactive fields — generates a `RuntimeState` interface with correct field types.
- Channel with no reactive fields — generates `runtimeState = null` in the output.
- Golden test — for a representative channel, the generated TS output byte-matches an expected fixture stored in `rxdjango/tests/fixtures/expected_reactive.ts`.

### 6.3 Integration test — `test_project/react_test/tests/test_runtime_state.py` (new)

Runs under `python manage.py test react_test.tests.test_runtime_state` from `test_project/`. Requires Redis and Mongo.

The test case exercises a realistic feature: **a typing indicator plus unread-count badge on the chat channel.** This covers single-field updates, container mutation, batching, and reconnection.

Add a reactive-enabled channel in `test_project/react_test/channels.py`:

```python
class JobContextChannel(ContextChannel):
    ...
    typing_users: list[int] = reactive(default_factory=list)
    unread_count: int = reactive(default=0)
    view_mode: str = reactive(default='view')

    @action
    async def start_typing(self, user_id: int):
        if user_id not in self.typing_users:
            self.typing_users.append(user_id)

    @action
    async def stop_typing(self, user_id: int):
        if user_id in self.typing_users:
            self.typing_users.remove(user_id)

    @action
    async def mark_all_read_and_switch_to_edit(self):
        async with self.batch():
            self.unread_count = 0
            self.view_mode = 'edit'
            self.typing_users.clear()
```

Integration test scenarios (using `WebsocketCommunicator`):

1. **Initial state includes reactive defaults** — Client connects, receives initial domain state, then receives exactly one `runtimeVars` message with `{typing_users: [], unread_count: 0, view_mode: 'view'}`. This message is sent immediately after initial state, before any other updates. Channels with zero reactive fields send no such message.

2. **Single scalar update** — Client calls action `start_typing(user_id=1)`. Client receives a `runtimeVar` message with `var='typing_users'`, `value=[1]`.

3. **Container mutation order** — Two sequential `start_typing` calls for different user IDs produce two `runtimeVar` messages, each with the correct cumulative list (`[1]`, then `[1, 2]`).

4. **Removal via in-place mutation** — `stop_typing(1)` after the above produces one `runtimeVar` with `[2]`.

5. **Batched update** — Client calls `mark_all_read_and_switch_to_edit`. Client receives exactly one `runtimeVars` message containing all three changed fields. No `runtimeVar` messages are sent during the batch.

6. **No broadcast when value unchanged** — Calling `start_typing(1)` when user 1 is already in `typing_users` produces no websocket message (the handler's `if` guard prevents the write; the descriptor's equality check is a secondary guarantee). Assert silence for 500ms after the action returns.

7. **Two clients, one channel** — Two WebSocket clients connected to the same job. Action from client A triggers `runtimeVar` delivery to both A and B.

8. **Reconnection resets reactive state** — Client A connects, calls `start_typing(1)` and verifies `typing_users=[1]`. Client A disconnects. A new connection from the same user is opened. The new connection receives `typing_users=[]` (the default), not `[1]`. This validates the per-connection contract: reactive fields are ephemeral session state, they do not persist across reconnects, and each new connection starts with fresh defaults. Additionally verify: if client B is connected throughout, B's view of reactive state is unaffected by A's reconnect (B still sees whatever B's own channel instance holds).

9. **TypeScript generation smoke test** — Run `makefrontend` against the test_project, assert the generated `react_test/react_test.channels.ts` includes a `JobContextChannelRuntimeState` interface with the three fields.

### 6.4 Frontend tests — `rxdjango-react/src/ContextChannel.test.ts`

Add to existing file:

- `subscribeRuntimeState` receives a single merged update when `runtimeVars` arrives.
- Multiple fields in one `runtimeVars` message result in exactly one listener call.
- `runtimeVar` (single) and `runtimeVars` (batch) update the same `runtimeState` object consistently.

Run: `cd rxdjango-react && yarn test --ci`.

## 7. Documentation updates

### 7.1 `docs/using-rxdjango.rst`

Replace the entire "Runtime State" section (lines 205-258). New content:

- Title: **Reactive State**
- Declare reactive fields at the class level with `reactive(...)`.
- Read and write as ordinary attributes.
- In-place mutation of lists/dicts/sets is detected automatically.
- Use `async with self.batch():` for atomic multi-field updates.
- Frontend usage — unchanged (`runtimeState` key returned from `useChannelState`).

### 7.2 `docs/context-channel.rst`

Replace the "RuntimeState class" section (lines 138-143) and "runtime_state" / "set_runtime_var" subsections (lines 359-369) with a single "Reactive fields" section referencing the new API.

### 7.3 `docs/api-reference.rst`

Remove the `set_runtime_var(var, value)` method doc at line 107. Add a `reactive(default=..., default_factory=...)` doc entry and a `batch()` doc entry. Remove the `RuntimeState` type parameter description at line 476 and replace with a paragraph about reactive field inference.

### 7.4 `CHANGELOG.md`

Add under `v0.3.0` heading:

```
- BREAKING: Removed `RuntimeState` nested class, `runtime_state` dict, and
  `set_runtime_var` method. Replaced with `reactive()` fields declared at
  the class level. See docs/using-rxdjango.rst for migration examples.
- Added `async with self.batch():` for atomic multi-field reactive updates,
  transmitted as a single `runtimeVars` websocket message.
- Added auto-proxy for `list`, `dict`, `set` reactive fields — in-place
  mutations broadcast automatically.
```

### 7.5 `CLAUDE.md`

Update any example snippets showing the old API (grep for `RuntimeState`, `runtime_state`, `set_runtime_var`).

## 8. Implementation order

Do it in this order. Run the relevant test suite at each checkpoint before moving on.

1. `rxdjango/state.py` — `reactive`, `ReactiveField`, container proxies, `BatchContext`, `MISSING`.
2. `rxdjango/tests/test_reactive.py` — all unit tests from 6.1. Must pass against the new module in isolation.
3. `rxdjango/channels.py` — metaclass wiring, `__init__` changes, `batch()` method, broadcast helpers. Delete `RuntimeState`, `runtime_state`, `set_runtime_var`.
4. `rxdjango/ts/channels.py` — generator reads `__reactive_fields__`.
5. `rxdjango/tests/test_ts_reactive_gen.py` — golden TS output.
6. `rxdjango-react/src/PersistentWebsocket.ts` and `ContextChannel.ts` — handle `runtimeVars`.
7. `rxdjango-react/src/ContextChannel.test.ts` — frontend tests. Run `yarn test --ci`.
8. Update `test_project/react_test/channels.py` with reactive fields.
9. `test_project/react_test/tests/test_runtime_state.py` — integration tests from 6.3. Run `python manage.py test react_test.tests.test_runtime_state`.
10. Docs — `using-rxdjango.rst`, `context-channel.rst`, `api-reference.rst`, `CHANGELOG.md`, `CLAUDE.md`.
11. Full suite green: `pytest rxdjango/tests/`, `cd test_project && python manage.py test react_test.tests`, `cd rxdjango-react && yarn test --ci`.
12. `flake8 --ignore=E501,W504 rxdjango`.
13. `cd rxdjango-react && npm run lint`.

## 9. Pinned decisions

These three questions were weighed and closed during design review. They are **not** open for the implementer to revisit without coming back to the author.

### 9.1 Reconnection behaviour — reactive state is per-connection, resets to defaults on reconnect

Reactive fields are ephemeral UI coordination state (typing indicators, current view mode, transient counters). They are not domain data. Domain data lives in models, is cached in Mongo, and is re-streamed on reconnect through the existing state flow.

On every new connection — initial or reconnect — the server constructs a fresh channel instance, reactive fields take their `default` / `default_factory` values, and the client receives them as a `runtimeVars` message immediately after initial state. The existing docs statement that "runtime state persists for one websocket connection" is preserved.

This keeps the contract simple and the implementation small. Persistence across reconnects would require answering: where is it stored, who owns it when the instance is gone, how do two reconnecting tabs reconcile, when does it expire. None of those questions have a clear answer yet, and adding persistence later via opt-in `reactive(persist=True)` is non-breaking. Deferred to a future release.

Integration test 6.3.8 enforces this behaviour.

### 9.2 Exception handling in `batch()` — transactional drop

If the body of `async with self.batch():` raises, the pending buffer is discarded: no values change on the instance, no websocket message is sent, the exception propagates. A `logging.warning` is emitted naming the channel class and the discarded field names, so swallowed exceptions upstream do not silently hide bugs.

The alternative (flush-on-exception) would emit a partial state that no handler intended to produce, and clients would see half-written updates. That is strictly worse than drop in every scenario we could construct.

To make this truly transactional, the buffer holds *pending* writes rather than applying them eagerly. Descriptor `__get__` inside an active batch returns the pending value if present, otherwise the stored value. Instance values and broadcast both commit on successful exit of the outermost batch. This matches what users expect from something called `batch()`.

Unit tests in 6.1 ("Batching" and the added bullets) enforce this.

### 9.3 Sync-context writes — raise `RuntimeError`, never queue or fallback

`ContextChannel` lives inside an `AsyncWebsocketConsumer`. Every legitimate entry point — `connect`, `receive`, `@consumer` handlers, `@action` handlers, channel lifecycle methods — runs with a running event loop accessible via `asyncio.get_running_loop()`. Sync helper methods called from those entry points also have access to the loop.

There is no legitimate code path where a reactive write originates from pure sync code with no loop. Writing reactive state from a Django signal handler is the wrong tool (use the channel layer or let the existing signal-based sync handle model-derived changes).

Therefore `__set__` calls `asyncio.get_running_loop()` unconditionally and, on `RuntimeError` (no loop), re-raises with a clear message naming the field. No send queue, no deferred flush, no silent drop. One obvious failure mode with one obvious fix.

Reference implementation:

```python
def __set__(self, instance, value):
    # Batch path — buffer the write and return
    if instance._batch_stack:
        instance._batch_stack[-1][self.name] = value
        return
    # Equality check against current stored value
    current = instance.__dict__.get(self.name, MISSING)
    if current is not MISSING and self._equal(current, value):
        return
    # Require a running event loop — fail loudly if absent
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        raise RuntimeError(
            f"Cannot set reactive field '{self.name}' outside an async context. "
            f"Reactive writes must originate from channel handlers "
            f"(@consumer, @action, or a lifecycle method)."
        )
    # Commit and schedule broadcast
    instance.__dict__[self.name] = value
    loop.create_task(instance._broadcast_field(self.name, value))
```

Unit tests in 6.1 (the added sync-context bullet) enforce this.

## 10. Out of scope

- Per-client reactive state (currently runtime state is per-connection; this stays).
- Computed/derived reactive fields (no `@reactive_property`). Can be added later without breaking this API.
- Persisting reactive state across reconnections beyond what the consumer already does.
