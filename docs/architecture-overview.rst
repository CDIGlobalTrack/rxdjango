
.. _architecture-overview:

=====================
Architecture Overview
=====================

The core of RxDjango is the `rxdjango.channel.ContextChannel` class.
On initialization, all apps are scanned for a file called `channels.py`,
and for *ContextChannel* subclass inside those files, and a set of signals
sigals are registered based on these classes.

Each *ContextChannel* subclass must contain a *Meta* class declaring
the *state* property, which must be an instance of `rest_framework.serializers.ModelSerializer`.
Usually, this is a nested serializer with many layers of serializers.
RxDjango splits the nested serializer into several flat serializers
and iterate to register a post_save signal for each of them (and maybe
a pre_save too, if the Meta class has *optimistic* set to True).

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

For each connected client, there is an instance on the backend ContextChannel in
a server, and for each ContextChannel class, there is a matching class in the frontend
providing an API to access that instance. The frontend class also contains a model of
the state, so it knows how to rebuild the nested structure from the flat serialized
layers, making cache very efficient. The state builder in the frontend class simulates
a reducer in react to trigger and change in context.

By the use of the `rxdjango.actions.action` decorator, developers can register methods
in the backend class that can be called asynchronously from the frontend class to change
the context and fetch data.

There is also the `rxdjango.consumers.consumer` decorator, which allows channels to
act as consumers, so that they can change the frontend state based on events on the
backend. (This is in a PR to be merged soon)

Finally, ContextChannel provides the `RuntimeState` interface, which allows arbitrary
runtime variables to be defined and set at the backend, to be automatically updated
in the frontend. (This is being implemented and will be available soon)

The final experience for the developer is a seamless integration between backend and
frontend, in which the need for Django views and React reducers is eliminated. The
instance state in the backend is automatically built and updated in the frontend,
and methods from the backend can be called directly from the frontend using the
established authenticated connection through a websocket.

RxDjango relies on `rest_framework.authtoken.models.Token` for authentication.
