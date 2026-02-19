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

      Clear the MongoDB cache for a specific anchor.

      :param anchor_id: The anchor ID whose cache should be cleared
      :returns: True if cache was cleared, False if rate-limited


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


@extend_ts
~~~~~~~~~~

.. py:decorator:: extend_ts(**fields)

   Add custom TypeScript properties to generated interfaces.

   :param fields: Mapping of field names to TypeScript type strings

   Example::

       @extend_ts(computed_field='string')
       class MySerializer(serializers.ModelSerializer):
           ...


@related_property
~~~~~~~~~~~~~~~~~

.. py:decorator:: related_property(accessor, reverse_accessor=None)

   Mark a Python ``@property`` on a model with the accessor path so
   RxDjango can track its dependencies.

   :param accessor: The query path to reach related instances
   :param reverse_accessor: The reverse path from the related model back


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

Redis (Coordination)
~~~~~~~~~~~~~~~~~~~~

Handles transient state and coordination:

- Cooldown tracking to rate-limit broadcasts per instance
- Timestamp generation for consistent ordering
- Channel-level state coordination


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

.. py:data:: RX_COOLDOWN_SECONDS
   :type: int
   :value: 1

   Minimum seconds between broadcasts for the same instance.

.. py:data:: RX_CACHE_TTL
   :type: int
   :value: 3600

   Seconds before cached state expires.

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
