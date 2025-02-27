
.. _caching:

=======
Caching
=======

RxDjango comes with a builtin cache system based on MongoDB, and it's
transparent to the application developers. This chapter explain this system.

In RxDjango the top-most instance of a state serializer from a channel
is called an **anchor**. RxDjango manages the cache for each anchor
separatly. For each channel there is a collection in MongoDB holding all
instances of that channel. Each anchor, for each channel, has one of four states:
COLD, HOT, HEATING, COOLING.

If cache is COLD, it means objects are in the database and need to be
fetched. HOT means instances are serialized and cached in MongoDB.
HEATING and COOLING means that state is transitioning between COLD and HOT.

During HEATING state, objects are copied to a queue in Redis and the size of
the queue is sent to a pubsub topic. A client that loads the state during HEATING
state will subscribe to the pubsub channel and then load all previous instances
from redis. So, if several clients connect at once, the first will get state
from database and distribute to others through redis while building cache.

The scenerio of several clients connected at once is common when a new anchor is
added to a channel with many instances. All connected clients are notified at
the same time and load the state at the same time.

This also happens on software releases. The cache is cleared whenever a
`manage.py migrate` command is executed, and as clients reconnect, they may request
the state of an anchor at the same time.

During COOLING state, objects are copied to a Redis queue with a pubsub much like
in HEATING. If a client connects during COOLING, the state is changed to HEATING
using the same Redis queue. This is the concept, but that transition is not well tested
yet (because release cleanup has been enough for us so far).

For every load of the state of a channel for an anchor, the state of the cache
for that anchor is checked and changed, in an atomic operation in Redis. Client
concurrency is synchronized by Redis, so that all clients always get the full state
no matter if from database or cache. This class responsible for that is
`rxdjango.state_loader.StateLoader`.
