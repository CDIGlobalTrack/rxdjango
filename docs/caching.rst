
.. _caching:

=======
Caching
=======

RxDjango comes with a builtin cache system based on MongoDB, and it's
transparent to the application developers. This chapter explains this system.

In RxDjango the top-most instance of a state serializer from a channel
is called an **anchor**. RxDjango manages the cache for each anchor
separately. For each channel there is a collection in MongoDB holding all
instances of that channel. Each anchor, for each channel, has one of four states:
COLD, HOT, HEATING, COOLING.

Cache States
============

If cache is COLD, it means objects are in the database and need to be
fetched. HOT means instances are serialized and cached in MongoDB.
HEATING and COOLING means that state is transitioning between COLD and HOT.

The full state machine is:

``COLD → HEATING → HOT → COOLING → COLD``

With one additional transition for the reheat path:

``COOLING → HEATING`` (when a client connects during COOLING)

HEATING
-------

During HEATING state, objects are copied to a queue in Redis and the size of
the queue is sent to a pubsub topic. A client that loads the state during HEATING
state will subscribe to the pubsub channel and then load all previous instances
from Redis. So, if several clients connect at once, the first will get state
from database and distribute to others through Redis while building cache.

The scenario of several clients connecting at once is common when a new anchor is
added to a channel with many instances. All connected clients are notified at
the same time and load the state simultaneously.

This also happens on software releases. The cache is cleared whenever a
``manage.py migrate`` command is executed, and as clients reconnect, they may request
the state of an anchor at the same time.

COOLING
-------

During COOLING state, instances are migrated from MongoDB to a Redis list
(similar to the HEATING queue). The COOLING process:

1. Atomically transitions the state from HOT to COOLING via a Lua script.
2. Reads all instances from MongoDB in batches, grouped by instance type.
3. Pushes each batch to the Redis instances list.
4. Deletes the batch from MongoDB after pushing to Redis.
5. Atomically finishes via a Lua script that checks the final state.

If a client connects during COOLING, the ``load()`` Lua script atomically
transitions the state to HEATING and increments the reader count. The COOLING
process detects this when it calls ``finish_cooling()``: instead of
transitioning to COLD, it **reheats** by writing all instances back to MongoDB,
then calls ``end_cold_session()`` to transition to HOT. This ensures clients
always receive consistent state regardless of when they connect.

All state transitions use atomic Lua scripts in Redis to prevent race conditions
between the expiry process, client connections, and concurrent operations.

For every load of the state of a channel for an anchor, the state of the cache
for that anchor is checked and changed, in an atomic operation in Redis. Client
concurrency is synchronized by Redis, so that all clients always get the full state
no matter if from database or cache. The class responsible for that is
``rxdjango.state_loader.StateLoader``.

Cache Expiry (TTL)
==================

RxDjango supports TTL-based cache expiry to reclaim MongoDB storage for anchors
that are no longer actively used.

Configuration
-------------

- **Global TTL**: Set ``RX_CACHE_TTL`` in Django settings (seconds). Default: 604800 (1 week).
- **Per-channel TTL**: Set ``Meta.cache_ttl`` on a ContextChannel subclass to override
  the global default for that channel.

.. code-block:: python

   # settings.py
   RX_CACHE_TTL = 86400  # 1 day global default

   # channels.py
   class MyContextChannel(ContextChannel):
       class Meta:
           state = MyNestedSerializer()
           cache_ttl = 3600  # Override: 1 hour for this channel

Session Tracking
----------------

Redis tracks active WebSocket connections per anchor via the ``sessions`` and
``last_disconnect`` keys:

- ``session_connect()``: Increments the session counter and clears ``last_disconnect``.
- ``session_disconnect()``: Decrements the session counter. When it reaches 0,
  records the current timestamp in ``last_disconnect``.

The TTL countdown starts when the last session disconnects. An anchor is eligible
for expiry only when ``sessions == 0`` and the time elapsed since ``last_disconnect``
exceeds the channel's TTL.

expire_rxdjango_cache Command
-----------------------------

The ``expire_rxdjango_cache`` management command scans all registered channels
for stale anchors and runs the COOLING cycle on each:

.. code-block:: bash

   # Expire all stale caches
   python manage.py expire_rxdjango_cache

   # Preview what would be expired
   python manage.py expire_rxdjango_cache --dry-run

Schedule it via cron or Celery beat:

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

The command is idempotent and safe to run concurrently — atomic Lua scripts
prevent double transitions.

Manual Expiry
-------------

You can manually clear the cache for a specific anchor:

.. code-block:: python

   await MyContextChannel.clear_cache(anchor_id)

This unconditionally transitions a HOT anchor through COOLING to COLD,
regardless of session count or TTL.

Cache Clearing on Migrate
=========================

The cache is automatically cleared whenever ``python manage.py migrate`` is
executed, via a ``post_migrate`` signal handler. This ensures that schema
changes don't cause stale cached data to be served.

Delta Computation
=================

When a model instance is saved, the signal handler serializes the new state
and writes it to MongoDB. Rather than broadcasting the full instance to
WebSocket clients, RxDjango computes a **delta** (minimal diff) between the
old and new cached documents.

- New instances (no previous version) are sent in full.
- Deleted instances are sent in full.
- Changed instances produce a minimal delta containing only modified fields,
  plus the required metadata (``_instance_type``, ``id``, ``_tstamp``, ``_operation``).

Delta computation uses a C extension (``delta_utils_c``) for performance when
available, with a pure-Python fallback (``delta_utils``). The C extension is
automatically compiled during ``pip install -e .``.

GridFS Fallback
===============

Documents that exceed MongoDB's 16MB document size limit are transparently
stored in GridFS. The MongoDB document is replaced with a lightweight reference
containing a ``_grid_ref`` field pointing to the GridFS file. When loading
state, instances with ``_grid_ref`` are fetched from GridFS and deserialized
back to their full form.

Transaction-Aware Broadcasting
==============================

The ``TransactionBroadcastManager`` ensures that broadcasts reflect the final
committed state of a database transaction, not mid-transaction snapshots.

Instead of serializing instance state immediately when ``save()`` is called,
the manager:

1. **Defers serialization**: Stores instance references (model class + ID) and
   serializes at ``transaction.on_commit()`` time when the committed state is
   available.
2. **Deduplicates**: Multiple saves of the same instance within a transaction
   result in a single broadcast (last operation wins).
3. **Thread-safe**: Uses thread-local storage for concurrent transaction support.

For delete operations, the serialized data and anchors are pre-computed at
signal time (since the instance won't exist after commit).
