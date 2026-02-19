
.. _architecture-overview:

=====================
Architecture Overview
=====================

Key Architectural Principles
============================

- **Serializers define both REST API structure and real-time state.** The same
  nested ``ModelSerializer`` that powers your REST endpoints defines the
  WebSocket state tree.
- **Instances are flattened and cached.** Nested serializer output is split into
  flat instances stored in MongoDB, then rebuilt on the client side.
- **Django signals trigger automatic updates.** Model ``save()`` and ``delete()``
  calls are intercepted to serialize changes, update the cache, and broadcast
  deltas to connected clients.
- **TypeScript interfaces are auto-generated.** Running ``makefrontend``
  introspects your serializers and channels to produce typed TypeScript code.
- **Each ContextChannel creates a stateful WebSocket connection** for a specific
  data context (one anchor instance or a list of instances).

How It Works
============

The core of RxDjango is the `rxdjango.channel.ContextChannel` class.
On initialization, all apps are scanned for a file called `channels.py`,
and for *ContextChannel* subclasses inside those files. Then, a set of signals
are registered based on these classes.

Each *ContextChannel* subclass must contain a *Meta* class declaring
the *state* property, which must be an instance of `rest_framework.serializers.ModelSerializer`.
Usually, this is a nested serializer with many layers of serializers.
RxDjango splits the nested serializer into several flat serializers
and iterate to register a post_save signal for each of them.

These signals will serialize each instance that composes the nested
serialization structure, broadcast them to all connected clients and
save them to MongoDB. So, generally, serialized flat instances are
delivered from MongoDB, but that depends on the cache state (see
:doc:`caching documentation <caching>`).

The ContextChannel provides the as_asgi() method, which registers channels
consumers bound to the ContextChannel. When developer runs
`manage.py makefrontend` (or runserver --makefrontend), it scans the
routing urlpatterns for registered ContextChannel and two files will be generated
for each app containing channels.py with ContextChannel classes in it:
`appname.interfaces.ts` and `appname.channels.ts`. The interfaces file
contains typescript interfaces for the nested serializers of each channel.
The channels file contains a class matching each ContextChannel
class.

For each connected client, there is an instance of the ContextChannel at
the backend, and for each ContextChannel class, there is a matching class in the frontend
providing an API to access that instance. The frontend class contains a model of
the state, so it knows how to rebuild the nested structure from the flat serialized
layers, making cache very efficient. The state builder in the frontend class simulates
a reducer in react to trigger changes.

By the use of the `rxdjango.actions.action` decorator, developers can register methods
in the backend class that can be called asynchronously from the frontend class to change
the context and fetch data.

There is also the `rxdjango.consumers.consumer` decorator, which allows channels to
implement consumer methods, so that they can change the frontend state based on events on the
backend.

Finally, ContextChannel provides the `RuntimeState` interface, which allows arbitrary
runtime variables to be defined and set at the backend, to be automatically updated
in the frontend.

The final experience for the developer is a seamless integration between backend and
frontend, in which the need for Django views and React reducers is eliminated. The
instance state in the backend is automatically built and updated in the frontend,
and methods from the backend can be called directly from the frontend using the
established authenticated connection through a websocket.

Transaction-Aware Broadcasting
===============================

RxDjango uses a ``TransactionBroadcastManager`` to ensure that broadcasts
reflect the final committed state of a database transaction, not mid-transaction
snapshots. When multiple model saves occur within a transaction, broadcasts
are deferred and deduplicated — only the final state is serialized and sent
at ``transaction.on_commit()`` time.

Authentication
==============

RxDjango relies on `rest_framework.authtoken.models.Token` for authentication.
