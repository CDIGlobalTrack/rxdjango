# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RxDjango is a real-time integration layer between Django and React that provides seamless bidirectional communication via WebSockets. It sits on top of Django Channels and Django REST Framework, automatically synchronizing backend state with React frontend using a nested serializer-based architecture.

**Key architectural principles:**
- Serializers define both REST API structure AND real-time state
- Instances are flattened and cached (MongoDB), then rebuilt on client
- Django signals trigger automatic cache updates and WebSocket broadcasts
- TypeScript interfaces are auto-generated from Django serializers
- Each `ContextChannel` creates a stateful WebSocket connection for a specific data context

## Development Commands

### Python/Django Backend

Build and install the package:
```bash
pip install -e .                      # Install in development mode (auto-compiles C extensions)
# Or build for distribution:
python -m build                       # Creates wheel and sdist in dist/
```

Generate frontend TypeScript files:
```bash
python manage.py makefrontend          # Generate all TS interfaces and channels
python manage.py makefrontend --dry-run   # Preview changes without writing
python manage.py makefrontend --force     # Force rebuild all files
python manage.py runserver --makefrontend # Auto-regenerate on changes during dev
```

Other commands:
```bash
python manage.py broadcast_system_message  # Broadcast to all connected clients
```

### React/TypeScript Frontend

Located in `rxdjango-react/`:
```bash
cd rxdjango-react
npm install              # or yarn install
npm run build           # Build TypeScript to dist/
npm run dev             # Watch mode for development
npm test                # Run Jest tests
npm run test:watch      # Watch mode for tests
npm run test:debug      # Debug tests with Node inspector
```

### Testing

**Frontend tests** (Jest):
```bash
cd rxdjango-react
yarn test --ci       # Run all tests (CI mode, no watch)
npm test             # Interactive/watch mode
```

**Frontend lint** (ESLint + TypeScript):
```bash
cd rxdjango-react
npm run lint
```

**Backend unit tests** (no external services needed):
```bash
pytest rxdjango/tests/
```

**Backend integration tests** (requires Redis on port 6379 and MongoDB on port 27017):
```bash
cd test_project
python manage.py test react_test.tests
python manage.py test react_test.tests.test_write_operations  # Write operation tests
```

**Backend lint** (flake8):
```bash
flake8 --ignore=E501,W504 rxdjango
```

### Building Distribution

```bash
python -m build  # Creates both wheel (.whl) and source distribution (.tar.gz) in dist/
```

Note: Requires the `build` package: `pip install build`

## Architecture Overview

### Core Components

**ContextChannel** (`rxdjango/channels.py`)
- The main API surface. Subclass this to create a real-time channel
- Each ContextChannel has a `Meta.state` that points to a ModelSerializer
- Metaclass `ContextChannelMeta` introspects the serializer and builds:
  - `StateModel`: Tree representing nested serializer structure
  - `WebsocketRouter`: Routes updates to connected clients
  - `SignalHandler`: Connects Django model signals to cache/broadcast system

**StateModel** (`rxdjango/state_model.py`)
- Recursively analyzes nested serializers to build a dependency tree
- Tracks relationships (ForeignKey, ManyToMany, OneToOne, custom properties)
- Each layer knows its `instance_type` (serializer path), `anchor_key` (query path), and children
- Provides `serialize_instance()` and `serialize_state()` for flattening instances
- Automatically exports TypeScript interfaces via `export_interface()`

**SignalHandler** (`rxdjango/signal_handler.py`)
- Monkey-patches `Model.save_base()` to attach `RxMeta` for tracking parent changes
- Connects to Django's `pre_save`, `post_save`, `pre_delete`, `post_delete` signals
- On model changes: serializes instance → writes to Mongo → broadcasts to WebSocket clients
- Handles parent relationship changes (e.g., moving an object to a different parent context)
- Uses `transaction.on_commit()` to ensure atomic updates

**StateConsumer** (`rxdjango/consumers.py`)
- AsyncWebsocketConsumer that handles WebSocket lifecycle
- Authenticates via `rest_framework.authtoken.models.Token`
- Loads initial state via `StateLoader`, then subscribes to real-time updates
- Supports `@action` decorated methods (RPC from frontend to backend)
- Supports `@consumer` decorated methods (subscribe to Django Channels groups)

**Caching System**
- **MongoDB** (`rxdjango/mongo.py`): Persistent cache of flattened instances with optimistic locking
- **Redis** (`rxdjango/redis.py`): Coordinates cooldowns and transient state
- Cache is cleared on `python manage.py migrate` via `post_migrate` signal

### TypeScript Generation

**Interface Export** (`rxdjango/ts/interfaces.py`)
- Introspects serializer fields and generates TypeScript interfaces
- Handles nested serializers, lists, optional fields, and type mappings
- Uses decorators like `@extend_ts()` to add custom TS properties

**Channel Export** (`rxdjango/ts/channels.py`)
- Generates TypeScript `ContextChannel` classes matching Django channels
- Exports `@action` methods as callable methods on frontend channel
- Generates proper type signatures from Python type hints

**Frontend SDK** (`rxdjango/sdk.py`)
- Orchestrates interface + channel generation via `make_sdk()`
- Uses git-aware diffing to show changes
- Writes to `RX_FRONTEND_DIR` configured in Django settings

### Frontend React Integration

**StateBuilder** (`rxdjango-react/src/StateBuilder.ts`)
- Receives flat instances from backend and rebuilds nested structure
- Handles create/update/delete operations efficiently
- Tracks `_tstamp` for optimistic updates and conflict resolution

**ContextChannel** (`rxdjango-react/src/ContextChannel.ts`)
- TypeScript base class for generated channel classes
- Manages WebSocket connection, authentication, reconnection
- Provides `useState()` hook (via `useChannelState` in `hook.ts`)

**PersistentWebsocket** (`rxdjango-react/src/PersistentWebsocket.ts`)
- Handles reconnection logic with exponential backoff
- Tracks `last_update` timestamp to fetch missed updates after reconnect

## Key Configuration

Required Django settings:
```python
INSTALLED_APPS = [
    'rxdjango',        # Must come before daphne
    'daphne',          # Must come before staticfiles
    'django.contrib.staticfiles',
    'channels',
]

ASGI_APPLICATION = 'your_project.asgi.application'
REDIS_URL = 'redis://127.0.0.1:6379/0'
MONGO_URL = 'mongodb://localhost:27017/'
MONGO_STATE_DB = 'hot_state'
RX_FRONTEND_DIR = os.path.join(BASE_DIR, '../frontend/src/app/modules')
RX_WEBSOCKET_URL = "http://localhost:8000/ws"
```

## Common Patterns

### Creating a ContextChannel

```python
# myapp/channels.py
from rxdjango.channels import ContextChannel
from myapp.serializers import MyNestedSerializer

class MyContextChannel(ContextChannel):
    class Meta:
        state = MyNestedSerializer()  # or MyNestedSerializer(many=True)
        auto_update = False  # Set True for many=True to auto-add/remove instances
        optimize_anchors = False  # Add _rx_* boolean field to model for filtering

    def has_permission(self, user, **kwargs):
        # Check if user can access this channel (called once on connect)
        return user.is_authenticated

    async def is_visible(self, instance_id):
        # For many=True channels: should this instance be visible?
        return True

    async def on_connect(self, tstamp):
        # Called after authentication and anchor initialization
        pass

    async def on_disconnect(self):
        # Cleanup when user disconnects
        pass
```

### Using @action decorator

```python
from rxdjango.actions import action

class MyContextChannel(ContextChannel):
    @action
    async def my_method(self, param1: str, param2: int) -> dict:
        # This will be callable from frontend as: await channel.myMethod(param1, param2)
        return {'result': 'success'}
```

### Using @consumer decorator

```python
from rxdjango.consumers import consumer

class MyContextChannel(ContextChannel):
    async def on_connect(self, tstamp):
        await self.group_add('my_group')  # Subscribe to group

    @consumer('my.event.type')
    async def handle_my_event(self, event):
        # This is called when 'my.event.type' is sent to 'my_group'
        await self.send(text_data=json.dumps(event))
```

### Serializer metadata

```python
class MySerializer(serializers.ModelSerializer):
    class Meta:
        model = MyModel
        fields = ['id', 'name', 'owner', ...]
        user_key = 'owner'  # Instances only sent to matching user
        optimistic = True   # Enable optimistic updates
        optimistic_timeout = 3  # Seconds before server state overrides
```

### Manually triggering broadcasts

```python
# From anywhere in your Django code:
from myapp.channels import MyContextChannel

MyContextChannel.broadcast_instance(anchor_id, instance, operation='update')
MyContextChannel.broadcast_notification(anchor_id, notification, user_id=None)
await MyContextChannel.clear_cache(anchor_id)  # Clear cache for a specific anchor
```

## Optimistic Updates (Write Operations)

RxDjango supports client-side write operations with optimistic updates, providing instant UI feedback before the server confirms the operation. This matches Django's ORM interface for a familiar developer experience.

### Overview

When a user modifies data in the UI, the changes are applied immediately (optimistic update), then reconciled when the server broadcast arrives. If the server rejects the operation, the client rolls back to the previous state.

### Backend Security

Write operations are protected by three layers, checked in this order:

1. **`Meta.writable` declaration** — Only serializer types explicitly listed in `Meta.writable` with the matching operation are accepted. Undeclared types or operations are rejected with a 403 before any DB access.
2. **Anchor context verification** — The server checks the MongoDB cache to confirm the target instance (or parent, for create) belongs to the channel's current anchor. This prevents a client connected to one anchor from modifying instances belonging to a different anchor.
3. **`can_*` authorization** — Per-operation authorization methods for custom business logic (ownership checks, role-based access, etc.).

### Writable Declaration

The `Meta.writable` dict controls which serializer types support which write operations. Types not listed are read-only. Operations not listed for a type are denied.

```python
from rxdjango.operations import SAVE, CREATE, DELETE

class MyContextChannel(ContextChannel):
    class Meta:
        state = MyNestedSerializer()
        writable = {
            TaskSerializer: [SAVE, CREATE, DELETE],
            AssetSerializer: [SAVE, DELETE],  # no create
            # DeadlineSerializer is in state but not writable
        }
```

### Authorization Methods

Override these methods for custom authorization logic:

```python
from rxdjango.operations import SAVE, CREATE, DELETE

class MyContextChannel(ContextChannel):
    class Meta:
        state = MyNestedSerializer()
        writable = {
            TaskSerializer: [SAVE, CREATE, DELETE],
        }

    def can_save(self, instance: Model, data: dict[str, Any]) -> bool:
        """Check if user can update an existing instance.
        
        Args:
            instance: The database instance (pre-update state)
            data: Partial field dict being applied
            
        Returns:
            True to allow, False to deny (rolls back optimistic update)
        """
        return instance.owner_id == self.user.id

    def can_create(
        self,
        model_class: type[Model],
        parent: Model,
        data: dict[str, Any],
    ) -> bool:
        """Check if user can create a new child instance.
        
        Args:
            model_class: The child model being created
            parent: The parent instance that owns the relation
            data: Field dict from the frontend
            
        Returns:
            True to allow, False to deny (removes temporary instance)
        """
        return parent.owner_id == self.user.id

    def can_delete(self, instance: Model) -> bool:
        """Check if user can delete an instance.
        
        Args:
            instance: The database instance to delete
            
        Returns:
            True to allow, False to deny (restores instance in UI)
        """
        return instance.owner_id == self.user.id
```

**Important**: All authorization methods default to `False` (deny all). You must explicitly override them to enable write operations.

### Backend Execution Flow

1. Client sends a `write` message with operation type and data
2. Server checks `Meta.writable` — rejects if type/operation not declared
3. Server checks MongoDB cache — rejects if instance not in channel's anchor context
4. Server loads the required ORM instance(s) from database
5. Server calls the appropriate `can_*` method for authorization
6. If denied: sends error response, client rolls back
7. If allowed: executes the ORM operation (`save()`, `create()`, `delete()`)
8. Django signals fire and broadcast canonical state to all clients

### WebSocket Message Protocol

**Save operation:**
```json
{
  "type": "write",
  "writeId": 123,
  "operation": "save",
  "instanceType": "myapp.serializers.TaskSerializer",
  "instanceId": 42,
  "data": {"name": "Updated Name"}
}
```

**Create operation:**
```json
{
  "type": "write",
  "writeId": 124,
  "operation": "create",
  "instanceType": "myapp.serializers.TaskSerializer",
  "parentType": "myapp.serializers.JobSerializer",
  "parentId": 1,
  "relationName": "tasks",
  "data": {"name": "New Task", "developer": 5}
}
```

**Delete operation:**
```json
{
  "type": "write",
  "writeId": 125,
  "operation": "delete",
  "instanceType": "myapp.serializers.TaskSerializer",
  "instanceId": 42
}
```

**Success response:**
```json
{
  "type": "writeResponse",
  "writeId": 123,
  "success": true
}
```

**Error response:**
```json
{
  "type": "writeResponse",
  "writeId": 123,
  "success": false,
  "error": {"code": 403, "message": "Permission denied"}
}
```

### Frontend Usage

When `Meta.writable` is declared, write methods are automatically attached to state objects by `StateBuilder` — no manual wrapping needed:

```typescript
// Methods appear automatically on state objects
await task.save({ name: 'Updated Name' });
await task.delete();
await job.tasks.create({ name: 'New Task', developer: devId });
```

For imperative write calls outside of state objects, use the low-level channel methods:

```typescript
// Save an existing instance
await channel.saveInstance(
  'myapp.serializers.TaskSerializer',
  taskId,
  { name: 'Updated Name' }
);

// Create a new child instance
await channel.createInstance(
  'myapp.serializers.TaskSerializer',
  'myapp.serializers.JobSerializer',
  jobId,
  'tasks',
  { name: 'New Task', developer: developerId }
);

// Delete an instance
await channel.deleteInstance(
  'myapp.serializers.TaskSerializer',
  taskId
);
```

### Rollback Behavior

- **save**: On error, restores previous field values
- **create**: On error, removes the temporary instance from the list
- **delete**: On error, re-inserts the instance at its original position

The server always broadcasts the canonical state after a successful operation, so the optimistic value is quietly replaced without visible flicker.

### Temporary IDs

For `create()` operations, the client generates a temporary negative integer ID (e.g., `-1`, `-2`) to allow React to render the instance before the server responds. The temporary entry is replaced with the real instance when the server broadcast arrives.

### Error Codes

| Code | Description |
|------|-------------|
| 400 | Bad request (invalid data, missing fields, instance not found) |
| 403 | Forbidden (authorization denied by `can_*` method) |
| 500 | Server error |

## Important Constraints

- **Always use `instance.save()`** for model updates. `YourModel.objects.update()` bypasses signals and breaks real-time sync.
- **Serializers must be nested `ModelSerializer`** instances. The anchor (top-level) must be a ModelSerializer.
- **Channel files must be named `channels.py`** in a Django app for auto-discovery.
- **Authentication is via `rest_framework.authtoken.models.Token`** only.
- **C extension compilation**: The `delta_utils` C extension is automatically compiled during `pip install -e .`.
- **Frontend regeneration**: Run `makefrontend` after any serializer or channel changes.
- **Related properties**: Custom properties must use `@related_property` decorator to specify reverse accessor.
- **Write operations require `Meta.writable`**: Only serializer types and operations explicitly declared in `Meta.writable` are accepted. The server also verifies the target instance belongs to the channel's anchor context via MongoDB cache lookup.

## File Organization

- `rxdjango/` - Core Django package
  - `channels.py` - ContextChannel base class and metaclass
  - `consumers.py` - StateConsumer and decorators (@action, @consumer)
  - `state_model.py` - Nested serializer introspection
  - `signal_handler.py` - Django signal wiring
  - `state_loader.py` - Initial state loading from cache
  - `actions.py` - @action decorator and RPC execution
  - `write.py` - Optimistic write operations (save, create, delete)
  - `mongo.py` - MongoDB cache layer
  - `redis.py` - Redis coordination layer
  - `exceptions.py` - Exception classes (WriteError, ForbiddenError, etc.)
  - `ts/` - TypeScript generation
    - `interfaces.py` - Serializer → TS interface
    - `channels.py` - ContextChannel → TS class
  - `management/commands/` - Django commands
  - `utils/` - Utilities including C extensions

- `rxdjango-react/` - React/TypeScript package
  - `src/ContextChannel.ts` - Base channel class with write methods
  - `src/StateBuilder.ts` - Flat → nested state rebuilding with optimistic updates
  - `src/PersistentWebsocket.ts` - Connection management
  - `src/hook.ts` - `useChannelState()` React hook
  - `src/actions.d.ts` - Type definitions for actions and writes
  - `src/ContextChannel.interfaces.ts` - Instance and model type definitions

- `docs/` - Sphinx documentation (RST format)

## Version and Releases

Current version: 0.0.46 (see `pyproject.toml`)

Published as:
- PyPI: `rxdjango` (Python package)
- npm: `@rxdjango/react` (React package)
