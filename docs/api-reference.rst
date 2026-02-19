API Reference
=============

This document consolidates all public APIs for RxDjango in one place.

.. contents:: Table of Contents
   :local:
   :depth: 2


ContextChannel (Backend)
------------------------

Base class for creating real-time channels.

.. py:class:: ContextChannel

   Base class for defining a real-time data channel. Subclass this in a
   file named ``channels.py`` inside a Django app.

   .. py:attribute:: Meta.state
      :type: ModelSerializer

      The serializer defining the state structure. Can be a single
      serializer or ``serializer(many=True)`` for list states.

   .. py:attribute:: Meta.auto_update
      :type: bool
      :value: False

      For ``many=True`` states, automatically add/remove instances
      when they match/unmatch the queryset.

   .. py:attribute:: Meta.optimize_anchors
      :type: bool
      :value: False

      Add ``_rx_*`` boolean fields to models for efficient filtering.
      When enabled, the metaclass adds a ``BooleanField`` (with ``db_index=True``)
      to the anchor model, allowing optimized queries for active channels.

   .. py:attribute:: Meta.cache_ttl
      :type: int | None
      :value: None

      Per-channel TTL override in seconds. When set, this channel's cache
      will expire after the specified duration instead of using the global
      ``RX_CACHE_TTL`` setting. Set to ``None`` (default) to use the global
      setting.

   .. py:method:: has_permission(user, **kwargs) -> bool

      Check if a user can access this channel.

      :param user: The authenticated Django user
      :param kwargs: URL parameters from the WebSocket route
      :returns: True if access is allowed

   .. py:method:: is_visible(instance_id) -> bool
      :async:

      For ``many=True`` channels, check if an instance should be
      visible to the connected user.

      :param instance_id: The ID of the instance to check

   .. py:method:: on_connect(tstamp)
      :async:

      Called after authentication succeeds. Use for initialization.

      :param tstamp: Last known timestamp if reconnecting, or None

   .. py:method:: on_disconnect()
      :async:

      Called when the WebSocket disconnects. Use for cleanup.

   .. py:method:: get_instance_id(**kwargs) -> int | str

      Return the anchor instance ID from URL parameters.
      Override to customize how the anchor ID is extracted.

      :param kwargs: URL parameters from the WebSocket route

   .. py:method:: list_instances(**kwargs) -> QuerySet
      :async:

      For ``many=True`` channels, return the queryset of anchor instances.
      Must be implemented by subclasses using ``many=True``.

   .. py:method:: add_instance(instance_id, at_beginning=False)
      :async:

      Add an instance to a ``many=True`` channel's list.

      :param instance_id: ID of the instance to add
      :param at_beginning: If True, prepend to the list

   .. py:method:: remove_instance(instance_id)
      :async:

      Remove an instance from a ``many=True`` channel's list.

      :param instance_id: ID of the instance to remove

   .. py:method:: set_runtime_var(var, value)
      :async:

      Set a runtime variable and push it to the connected client.

      :param var: Variable name
      :param value: Variable value (must be JSON-serializable)

   .. py:method:: send(*args, **kwargs)
      :async:

      Proxy to the underlying ``AsyncWebsocketConsumer.send()``.
      Use this to send arbitrary data to the connected client.

   .. py:method:: group_add(group)
      :async:

      Add this consumer to a Django Channels group. Use this in
      ``on_connect()`` to subscribe to group events handled by
      ``@consumer`` decorated methods.

      :param group: The group name to join

   .. py:method:: serialize_instance(instance, tstamp=0) -> dict
      :async:

      Serialize a model instance using the channel's state model.
      Returns a flat dictionary suitable for broadcasting.

      :param instance: The Django model instance to serialize
      :param tstamp: Timestamp to attach to the serialized data

   .. py:classmethod:: broadcast_instance(anchor_id, instance, operation='update')

      Manually broadcast an instance update to all connected clients.

      :param anchor_id: The anchor (root object) ID for the channel
      :param instance: The model instance to broadcast
      :param operation: ``'create'``, ``'update'``, or ``'delete'``

   .. py:classmethod:: broadcast_notification(anchor_id, notification, user_id=None)

      Send a notification to connected clients.

      :param anchor_id: The anchor ID to target
      :param notification: Dict with notification data
      :param user_id: If provided, only send to this user

   .. py:classmethod:: clear_cache(anchor_id)
      :async:

      Clear the MongoDB cache for a specific anchor via the COOLING state.

      Transitions HOT → COOLING, migrates instances from MongoDB to a Redis
      list (so clients that connect during the process can still read state),
      then transitions to COLD. If a client connects during COOLING, the state
      transitions to HEATING and instances are reheated (written back to MongoDB).

      :param anchor_id: The anchor ID whose cache should be cleared
      :returns: True if cache was cleared (or reheated), False if not HOT

   .. py:classmethod:: get_cache_ttl() -> int

      Get the cache TTL in seconds for this channel.

      Resolution order:

      1. ``Meta.cache_ttl`` on the channel class (per-channel override)
      2. ``RX_CACHE_TTL`` Django setting (global override)
      3. Default: 604800 (1 week)

   .. py:classmethod:: get_registered_channels() -> set

      Return all registered ContextChannel subclasses. Useful for
      introspection and cache management tooling.


Decorators
----------

@action
~~~~~~~

.. py:decorator:: action

   Expose a ContextChannel method as a frontend-callable RPC action.

   The method must be ``async``. It will be available on the generated
   TypeScript channel class with automatic parameter serialization.
   Type hints are inspected to auto-convert parameters (e.g. ``datetime``
   strings are converted to ``datetime`` objects).

   Example::

       @action
       async def update_status(self, status: str) -> dict:
           self.instance.status = status
           self.instance.save()
           return {"success": True}

   Frontend usage::

       const result = await channel.updateStatus("active");


@consumer
~~~~~~~~~

.. py:decorator:: consumer(event_type)

   Subscribe a ContextChannel method to Django Channels group events.

   The decorated method will be called when an event of the specified
   type is received on any group the consumer is subscribed to.

   :param event_type: The event type string to listen for

   Example::

       async def on_connect(self, tstamp):
           await self.group_add('chat_room_123')

       @consumer('chat.message')
       async def handle_chat(self, event):
           await self.send(text_data=json.dumps(event['data']))


@related_property
~~~~~~~~~~~~~~~~~

.. py:decorator:: related_property(accessor, reverse_accessor=None)

   Mark a Python ``@property`` on a model with the accessor path so
   RxDjango can track its dependencies. This is required for custom
   model properties that are included in serializers — without it,
   RxDjango cannot determine which related model changes should trigger
   re-serialization.

   The decorator replaces the function with a standard Python ``property``
   and registers the accessor paths in a global registry used by
   ``StateModel`` during serializer introspection.

   :param accessor: The query path to reach related instances (e.g. ``'items'``)
   :param reverse_accessor: The reverse path from the related model back
       (e.g. ``'order'``)

   Example::

       from rxdjango.decorators import related_property

       class Order(models.Model):
           @related_property('items', 'order')
           def total_price(self):
               return sum(item.price for item in self.items.all())


Serializer Meta Options
-----------------------

RxDjango extends the standard DRF serializer ``Meta`` class with
additional options for real-time behavior.

.. py:attribute:: Meta.user_key
   :type: str

   Field name that identifies the owning user. Instances are only
   sent to the user matching this field value.

   Example::

       class TaskSerializer(serializers.ModelSerializer):
           class Meta:
               model = Task
               fields = ['id', 'title', 'owner']
               user_key = 'owner'

.. py:attribute:: Meta.optimistic
   :type: bool
   :value: False

   Enable optimistic updates. The frontend can immediately reflect
   changes before server confirmation.

.. py:attribute:: Meta.optimistic_timeout
   :type: int
   :value: 3

   Seconds before server state overrides optimistic updates.


Exceptions
----------

All exceptions are defined in ``rxdjango.exceptions``.

.. py:exception:: UnknownProperty

   Raised when a serializer references a model property that does not exist.
   Typically indicates a missing ``@related_property`` decorator on a custom
   model property.

.. py:exception:: AnchorDoesNotExist

   Raised when the requested anchor object cannot be found during WebSocket
   connection. Occurs when the anchor ID from the URL route does not match
   any database record.

.. py:exception:: UnauthorizedError

   Raised when authentication fails due to a missing or invalid token.
   Results in a 401 status code sent to the client.

.. py:exception:: ForbiddenError

   Raised when an authenticated user lacks permission to access a channel.
   Occurs when ``has_permission()`` returns ``False``, resulting in a 403
   status code.

.. py:exception:: RxDjangoBug

   Raised when an internal invariant is violated. Indicates a bug in
   RxDjango itself. If encountered, please report it as an issue.

.. py:exception:: ActionNotAsync

   Raised during channel initialization when an ``@action``-decorated method
   is not defined with ``async def``. All action methods must be async.


WebSocket Protocol
------------------

The WebSocket message format used between the Django backend and React
frontend.

Incoming Messages (Client → Server)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Authentication (must be the first message after connecting)::

    {"token": "<rest_framework_auth_token>", "last_update": <timestamp|null>}

Action call (RPC)::

    {"callId": <unique_id>, "action": "methodName", "params": [...]}

Outgoing Messages (Server → Client)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Connection status::

    {"status_code": 200}
    {"status_code": 401, "error": "error/unauthorized"}
    {"status_code": 403, "error": "error/forbidden"}
    {"status_code": 404, "error": "error/not-found"}

Initial anchor list (sent after authentication)::

    {"initialAnchors": [1, 2, 3]}

State instances (array of flat instances)::

    [{"id": 1, "_instance_type": "app.Serializer", "_tstamp": ..., ...}, ...]

End of initial state marker::

    [{"_instance_type": "", "_tstamp": ..., "_operation": "end_initial_state", "id": 0}]

Action response::

    {"callId": <id>, "result": ...}
    {"callId": <id>, "error": "Error"}

Runtime state change::

    {"runtimeVar": "varName", "value": ...}

Prepend anchor (for ``many=True`` channels)::

    {"prependAnchor": <anchor_id>}


Frontend API (TypeScript)
-------------------------

ContextChannel
~~~~~~~~~~~~~~

.. js:class:: ContextChannel<T, Y>

   Base class for generated TypeScript channel classes. Manages
   WebSocket connection, authentication, state rebuilding, and RPC.

   :param T: Type of the root/anchor state
   :param Y: Type of the runtime state (optional)

   .. js:method:: constructor(token)

      :param token: Django REST Framework auth token

   .. js:method:: init()

      Initialize the WebSocket and StateBuilder. Called automatically
      on first ``subscribe()``.

   .. js:method:: disconnect()

      Gracefully close the WebSocket connection.

   .. js:method:: subscribe(listener, noConnectionListener?)

      Subscribe to state changes. Auto-connects on first subscriber.

      :param listener: Callback invoked with new state on every update
      :param noConnectionListener: Optional callback for connection status
      :returns: Unsubscribe function

   .. js:method:: subscribeInstance(listener, instance_id, instance_type)

      Subscribe to changes on a specific instance.

      :param listener: Callback invoked when instance changes
      :param instance_id: The instance ID
      :param instance_type: The ``_instance_type`` string
      :returns: Unsubscribe function

   .. js:method:: getInstance(instance_type, instance_id)

      Get a specific instance from state.

      :param instance_type: The ``_instance_type`` string
      :param instance_id: The instance ID
      :returns: The instance, or null if not found

   .. js:method:: callAction(action, params)

      Call a backend ``@action`` method via WebSocket RPC.
      Used internally by generated subclass methods.

      :param action: The snake_case action name
      :param params: Array of parameters
      :returns: Promise resolving with the action's return value


useChannelState
~~~~~~~~~~~~~~~

.. js:function:: useChannelState(channel)

   React hook for subscribing to channel state.

   :param channel: A ContextChannel instance, or undefined
   :returns: Object with ``state``, ``connected``, ``no_connection_since``,
             ``runtimeState``, ``empty``, and ``error`` fields


useChannelInstance
~~~~~~~~~~~~~~~~~~

.. js:function:: useChannelInstance(channel, instance_type, instance_id)

   React hook for subscribing to a specific instance.

   :param channel: A ContextChannel instance, or undefined
   :param instance_type: The ``_instance_type`` string
   :param instance_id: The instance ID, or undefined
   :returns: The instance, or null if not available


StateBuilder
~~~~~~~~~~~~

.. js:class:: StateBuilder<T>

   Reconstructs nested state from flat server instances.

   Maintains an instance registry indexed by ``_instance_type:id`` and
   rebuilds the nested structure by replacing foreign key IDs with
   object references. Reference changes propagate recursively to
   trigger React re-renders.

   .. js:method:: update(instances)

      Process a batch of instance updates from the server.

      :param instances: Array of flat instances with ``_instance_type``

   .. js:attribute:: state

      The current rebuilt nested state. Returns a new reference on each
      access to trigger React re-renders.


PersistentWebSocket
~~~~~~~~~~~~~~~~~~~

.. js:class:: PersistentWebSocket

   WebSocket wrapper with automatic reconnection and message routing.

   Features exponential backoff retry on connection drops, and routes
   incoming messages to type-specific handlers.

   .. js:method:: connect()

      Initiate WebSocket connection and send auth token.

   .. js:method:: send(data)

      Send a string message through the WebSocket.

   .. js:method:: disconnect(reason?)

      Close the connection. If reason is provided, prevents reconnection.


Cache System
------------

RxDjango uses a two-layer cache for performance:

MongoDB (Persistent State)
~~~~~~~~~~~~~~~~~~~~~~~~~~

Stores flattened instance data with optimistic locking.

- One collection per channel (named ``<channel_name>``)
- Documents contain serialized instance data plus metadata
- ``_tstamp`` field for change tracking and incremental loading
- Optimistic locking prevents concurrent write conflicts
- Documents exceeding MongoDB's 16MB limit are transparently stored
  in GridFS and referenced via a ``_grid_ref`` field

Redis (Coordination)
~~~~~~~~~~~~~~~~~~~~

Handles transient state and coordination:

- Cache state management (COLD / HEATING / HOT / COOLING)
- Active WebSocket session tracking per anchor
- ``last_disconnect`` timestamps for TTL-based expiry
- Instance lists during HEATING and COOLING transitions
- Pub/sub triggers for coordinating concurrent state loads
- Timestamp generation for consistent ordering


Management Commands
-------------------

expire_rxdjango_cache
~~~~~~~~~~~~~~~~~~~~~

Expire stale caches that have exceeded their configured TTL.

.. code-block:: bash

    # Expire all stale caches
    python manage.py expire_rxdjango_cache

    # Preview what would be expired without making changes
    python manage.py expire_rxdjango_cache --dry-run

This command is idempotent and safe to run concurrently (atomic Lua scripts
prevent double transitions). Schedule it via cron or Celery beat:

.. code-block:: bash

    # cron - run every 5 minutes
    */5 * * * * cd /path/to/project && python manage.py expire_rxdjango_cache

.. code-block:: python

    # Celery beat
    CELERY_BEAT_SCHEDULE = {
        'expire-rx-caches': {
            'task': 'myapp.tasks.expire_rx_caches',
            'schedule': 300,
        },
    }

broadcast_system_message
~~~~~~~~~~~~~~~~~~~~~~~~

Broadcast a message to all connected WebSocket clients via the system channel.

.. code-block:: bash

    python manage.py broadcast_system_message <source> <message>

The ``source`` parameter identifies the type of message (e.g. ``maintenance``).
The ``message`` parameter is the human-readable message text.

makefrontend
~~~~~~~~~~~~

Generate TypeScript interfaces and channel classes from Django serializers
and ContextChannel subclasses.

.. code-block:: bash

    python manage.py makefrontend              # Generate all TS files
    python manage.py makefrontend --dry-run    # Preview changes
    python manage.py makefrontend --force      # Force rebuild all files


Configuration
-------------

Required Django Settings
~~~~~~~~~~~~~~~~~~~~~~~~

.. py:data:: REDIS_URL
   :type: str

   Redis connection URL.

   Example: ``redis://127.0.0.1:6379/0``

.. py:data:: MONGO_URL
   :type: str

   MongoDB connection URL.

   Example: ``mongodb://localhost:27017/``

.. py:data:: MONGO_STATE_DB
   :type: str

   MongoDB database name for state cache.

   Example: ``hot_state``

.. py:data:: RX_FRONTEND_DIR
   :type: str

   Path where ``makefrontend`` writes generated TypeScript files.

.. py:data:: RX_WEBSOCKET_URL
   :type: str

   WebSocket URL for frontend connections.

   Example: ``http://localhost:8000/ws``

Optional Settings
~~~~~~~~~~~~~~~~~

.. py:data:: RX_CACHE_TTL
   :type: int
   :value: 604800

   Global cache TTL in seconds. When no active WebSocket sessions are
   connected to an anchor, the TTL countdown begins from the last
   disconnect time. After the TTL elapses, the ``expire_rxdjango_cache``
   command will transition the cache from HOT to COLD.

   Defaults to 604800 (1 week). Can be overridden per-channel via
   ``Meta.cache_ttl`` on the ContextChannel subclass.

.. py:data:: RXDJANGO_SYSTEM_CHANNEL
   :type: str
   :value: '_rxdjango_system'

   Name of the Django Channels group used for system-wide broadcasts
   (e.g. maintenance messages via ``broadcast_system_message``).

.. py:data:: TESTING
   :type: bool
   :value: False

   When ``True`` (along with ``DEBUG``), the state loader annotates
   initial state instances with a ``_cache_state`` field indicating
   whether the data came from COLD, HEATING, or HOT cache. Useful
   for debugging cache behavior during development.

Required Django Apps
~~~~~~~~~~~~~~~~~~~~

::

    INSTALLED_APPS = [
        'rxdjango',        # Must come before daphne
        'daphne',          # Must come before staticfiles
        'django.contrib.staticfiles',
        'channels',
    ]

    ASGI_APPLICATION = 'your_project.asgi.application'
