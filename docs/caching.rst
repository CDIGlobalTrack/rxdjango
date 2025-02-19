
.. _caching:

=======
CACHING
=======

RxDjango comes with a builtin cache system based on MongoDB, and it's
transparent to the application developers. This chapter explain this system.

In RxDjango the top-most instance of a state serializer from a channel
is called an *anchor*. RxDjango manages the cache for each anchor
separatly. For each channel there is a collection in MongoDB holding all
instances of that channel. Each anchor, for each channel, has one of four states:
COLD, HOT, HEATING, COOLING.

If cache is COLD, it means objects are in the database and need to be
fetched. HOT means instances are serialized and cached in MongoDB.
HEATING and COOLING means that state is transitioning between COLD and HOT.

During HEATING state, objects are copied to a queue in Redis and sent to a
pubsub topic. A client that loads the state during HEATING state will subscribe
to the pubsub channel and then load all previous instances from redis. So,
if several clients connect at once, only the first will get state from database.

The scenerio of several clients connected at once is particularly common during
software releases. The MongoDB cache is cleared whenever a `manage.py migrate`
command is executed.

During COOLING state, objects are copied to a Redis queue with a pubsub much like
in HEATING. If a client connects during COOLING, the state is changed to HEATING
using the same Redis queue. This is the concept, but that transition is not implemented
yet (because release cleanup has been enough for us so far).

For every load of the state of a channel for an anchor, the state of the cache
for that anchor is checked and changed, in an atomic operation in Redis. Client
concurrency is synchronized by Redis, so that all clients always get the full state
no matter if from database or cache. This class responsible for that is
`rxdjango.state_loader.StateLoader`.
