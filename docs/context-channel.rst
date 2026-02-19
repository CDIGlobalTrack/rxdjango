
.. _context-channel:

========================
The ContextChannel class
========================

The `rxdjango.channels.ContextChannel` is the core of the RxDjango. Every
ContextChannel subclass must declare a `Meta` class containing a `state` property,
which must be an instance of a `serializers.ModelSerializer` subclass.

Each ContextChannel is either a single instance channel, or a multiple instance
channel. This is defined by the parameter `many` in the state.

Single instance channels
========================

In single instance channels, the state relayed to the frontend
will be a dictionary. Single instance channels should implement
the static method `has_permission`:

.. code-block:: python

   from rxdjango.channels import ContextChannel
   from myapp.serializers import MyNestedSerializer

   class MyContextChannel(ContextChannel):

       class Meta:
           state = MyNestedSerializer()

       @staticmethod
       def has_permission(user, instance_id):
           # Check permission

Multiple instance channels
==========================

In multiple instances channels, the state relayed to the frontend
will be a list of dictionaries. Multiple instance channels must
implement the `list_instances` method, which should return
a queryset.

.. code-block:: python

   from rxdjango.channels import ContextChannel
   from myapp.serializers import MyNestedSerializer

   class MyContextChannel(ContextChannel):

       class Meta:
           state = MyNestedSerializer(many=True)

       def list_instances(self):
           # Filter objects user can see, hypothetically
           return MyModel.objects.filter(user=self.user)

Meta class
==========

state
-----

The state property must be set to a `serializers.ModelSerializer
<https://www.django-rest-framework.org/api-guide/serializers/#modelserializer>`_
instance. If `many=True` is passed to the serializer, this channel will be
a **multiple instances channel**, otherwise, it will be a **single instance channel**.
This affects which methods will be called

auto_update
-----------

On multiple instances channels, setting `auto_update` to `True` will
automatically add new instances to this channel when they are created.

The method `is_visible` will be called to check if the newly created instance
should be added or not. Instances are added to the beginning of the state list.

Attention: If each connected client creates instances often, this has performance
of O(N²), as each instance will be checked by each connected client.

optimize_anchors
----------------

When `optimize_anchors` is set to `True`, the metaclass adds a boolean field
(``_rx_<module>_<channelname>``) with ``db_index=True`` to the anchor model.
This allows efficient database queries to filter only active anchors, improving
performance when many anchor instances exist but only a few have active
WebSocket connections.

cache_ttl
---------

Per-channel cache TTL override in seconds. When set, this channel's cache will
expire after the specified duration instead of using the global ``RX_CACHE_TTL``
setting. Defaults to ``None`` (use global setting).

.. code-block:: python

   class MyContextChannel(ContextChannel):

       class Meta:
           state = MyNestedSerializer()
           cache_ttl = 3600  # Expire after 1 hour instead of the global default

RuntimeState class
==================

A class named `RuntimeState`, inheriting `typing.TypedDict`, may be declared
inside the ContextChannel subclass. It's a flat dictionary, accessible through
`self.runtime_state`. Its values can be changed using `set_runtime_var`.

Methods
=======

has_permission
--------------

This static method will be called after user has been authenticated
and before sending the state to the frontend.

The first parameter passed to `has_permission` is the user instance.
Other parameters will be `**kwargs` coming from the url route for
this channel.

Returns True by default.

get_instance_id
---------------

This method is called in single instance channels, after user has been
authenticated and permission checked. It receives `**kwargs` coming from
the url route for this channel.

By default, it returns the first argument, so it will work if you have
the id as parameter, which is the most common case.

list_instances
--------------

This method is called in multiple instance channels, after user has been
authenticated and permission checked.

It should return a queryset (and support for returning a list of ids will
come soon).

add_instance
------------

For multiple instances channels, this method adds a new instance to state.
It should be given the instance id. If the at_beginning parameter is True,
the instance will be added as the first element of the state.

remove_instance
---------------

For multiple instances channels, this method removes an instance from the state.

clear
-----

For multiple instances channels, this method removes all instances from the state.

is_visible
----------

If `Meta.auto_updated` is set to True, this method is called for each new instance
of the state model created. If it returns True, the instance will be automatically
added at the beginning of the list.

on_connect
----------

This method is called after user has been authenticated.

It receives the tstamp of the last update this client had, in case this is a reconnection,
but on client side this is not implemented yet, so tstamp is always None for now.

on_disconnect
-------------

This method is called when client disconnects.

group_add
---------

Add this consumer to a Django Channels group. Call this in ``on_connect()`` to
subscribe to events that will be handled by ``@consumer`` decorated methods.

.. code-block:: python

   async def on_connect(self, tstamp):
       await self.group_add('my_group')

send
----

Proxy to the underlying ``AsyncWebsocketConsumer.send()``. Use this to send
arbitrary JSON data to the connected client.

serialize_instance
------------------

Serialize a model instance using the channel's state model. Returns a flat
dictionary suitable for broadcasting. This is an async method that wraps
the state model's synchronous serialization in ``database_sync_to_async``.

clear_cache
-----------

This classmethod receives the id of an anchor in this channel and clears the cache
for that anchor via the COOLING state. Returns ``True`` if the cache was cleared,
``False`` if the anchor was not in HOT state.

Class Methods
=============

broadcast_instance
------------------

Manually broadcast an instance update to all connected clients for a given anchor.

.. code-block:: python

   MyContextChannel.broadcast_instance(anchor_id, instance, operation='update')

The ``operation`` parameter can be ``'create'``, ``'update'``, or ``'delete'``.

broadcast_notification
----------------------

Send a notification to connected clients for a given anchor.

.. code-block:: python

   MyContextChannel.broadcast_notification(anchor_id, {'message': 'Hello'}, user_id=None)

If ``user_id`` is provided, the notification is sent only to that user.

get_cache_ttl
-------------

Returns the cache TTL in seconds for this channel, checking ``Meta.cache_ttl``
first, then the global ``RX_CACHE_TTL`` setting, then defaulting to 1 week.

get_registered_channels
-----------------------

Returns the set of all registered ContextChannel subclasses.

runtime_state
-------------

This property is a dictionary, containing the runtime state of the application, in case the
`RuntimeState` class has been defined. It should be updated using `set_runtime_var` method,
so changes are relayed to the frontend.

set_runtime_var
---------------

This sets one runtime variable, which will be relayed to the frontend and updated there.

Serializer Meta Options
=======================

RxDjango extends the standard DRF serializer ``Meta`` with additional options
that control real-time behavior for individual serializer layers.

user_key
--------

Field name that identifies the owning user. When set, instances are only
sent to the WebSocket client whose user matches this field value.

.. code-block:: python

   class TaskSerializer(serializers.ModelSerializer):
       class Meta:
           model = Task
           fields = ['id', 'title', 'owner']
           user_key = 'owner'

optimistic
----------

Enable optimistic updates. When ``True``, the frontend can immediately
reflect changes before server confirmation. Defaults to ``False``.

optimistic_timeout
------------------

Seconds before server state overrides optimistic updates. Defaults to ``3``.
