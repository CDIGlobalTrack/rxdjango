
:: _using-rxdjango:

==============
Using RxDjango
==============

Backend
-------

The `rxdjango.channels.ContextChannel` is the core of the RxDjango. Every
ContextChannel subclass must declare a `Meta` class containing a `state` property

.. code-block:: python

    from rxdjango.channels import ContextChannel
    from myapp.serializers import MyNestedSerializer

    class MyContextChannel(ContextChannel):

        class Meta:
            state = MyNestedSerializer()

A ContextChannel has to be either a single or a multiple instance channel.
A single instance channel, like the one above, must define the `has_permission`
static method, to verify if user has permission on an instance:

.. code-block:: python

    from rxdjango.channels import ContextChannel
    from myapp.serializers import MyNestedSerializer

    class MyContextChannel(ContextChannel):

        class Meta:
            state = MyNestedSerializer()

        @staticmethod
        def has_permission(user, **kwargs)

        def get_instance_id(self, **kwargs):
            # this is optional, by default returns the first kwarg
            return kwargs['my_model_id']

To declare a channel that has several instances, `many=True` has to be passed to
the Meta.state serializer. In this case, the method `list_instances()` must be
implemented, and it should return either a queryset or a list of instance ids.

.. code-block:: python

    from rxdjango.channels import ContextChannel
    from myapp.serializers import MyNestedSerializer
    from mayapp.models import MyModel()

    class MyContextListChannel(ContextChannel):

        class Meta:
            state = MyNestedSerializer(many=True)

        async def list_instances(self, **kwargs):
            # Return a queryset of visible objects
            # You may need to use @database_sync_to_async

In both cases, `**kwargs` comes from the paths registered in asgi.py:

.. code-block:: python

    from myapp.channels import MyContextChannel

    websocket_urlpatterns = [
        path('ws/myapp/instance/<str:mymodel_id>/', MyContextChannel.as_asgi()),
        path('ws/myapp/list', MyContextListChannel.as_asgi()),
    ]

    application = ProtocolTypeRouter({
        "http": app,
        "websocket": URLRouter(
            websocket_urlpatterns
        ),
    })

Frontend
--------

Once the channels and routes are created, you can run the `makefrontend` command to
generate the typescript interfaces and classes at the frontend. Make sure you have
configured `settings.RX_FRONTEND_DIR` and `settings.RX_WEBSOCKET_URL`.

.. code-block:: bash

    ./manage.py makefrontend

The output of the command will be a diff of the frontend files. If you want to
automatically build frontend as you develop, you can use the `--makefrontend`
options for runserver:

.. code-block:: bash

    ./manage.py runserver --makefrontend

Make sure you have installed `@rxdjango/react` dependency in the frontend.
Below is an example on how to use the state in the frontend.

.. code-block:: typescript

    import { useChannelState } from "@rxdjango/react";
    import { MyNestedType } from "my-rx-frontend-dir/myapp.interfaces";
    import { MyContextChannel } from "my-rx-frontend-dir/myapp.channels";

    const MyPage = () => {
      const channel = new MyContextChannel(instanceId, auth.token);
      const { state } = useChannelState<MyNestedType>(channel);
    }

Now the `state` variable will be automatically updated with the state of the instance
as it is updated in the models.

Actions
-------

Actions operate on both backend and frontend. With actions, methods can be registered
on the backend to be called directly from the frontend.

On the backend side:

.. code-block:: python

    from rxdjango.actions import action

    class MyContextListChannel(ContextChannel):

        ...

        @action
        async def change_instance_state(self, some_var: int) -> bool:
            # do something, changes in state will automatically be broadcast
            return result

When creating actions, it's important to use typehints, so typescript interfaces
can automatically be generated for the frontend.

In channels with a list of instances, actions can be used to change the
instances in the context, for example to create a search:

.. code-block:: python

    from rxdjango.actions import action

    class MyContextListChannel(ContextChannel):

        search_term = None

        @action
        async def search(self, term: str) -> None:
            self.search_term = term
            instances = self._list_instances()
            self.clear()
            for instance in instances:
                self.add_instance(instance)

`add_instance`, `remove_instance` and `clear` methods can be used to change
the instances in the context, for channels with a list of instances.

On the frontend side, a method will be created in the channel class. When the
method is called from the frontend, it will be asynchronously called in the
backend, and the results will be returned to the frontend.

.. code-block:: typescript

    const channel = new MyContextChannel(instanceId, auth.token);

    await channel.search(searchTerm);

Consumers
---------

RxDjango is build on top of `Django Channels <https://channels.readthedocs.io/>`_,
which implements the concept of consumers. Each `ContextChannel` instance has
a private instance of `AsyncWebsocketConsumer`, and provides an interface to it.

You can implement consumer functionality by using the `rxdjango.consumers.consumer`
decorator on your `ContextChannel`:

.. code-block:: python

    from rxdjango.channels import ContextChannel
    from rxdjango.consumers import consumer

    class MyChannel(ContextChannel):

        @consumer('some.event.type')
        def my_consumer(self, event):
            # handle event

        async def on_connect(tstamp):
            # Join a group to receive events
            await self.group_add('some-group')

For this to work, you will probably want to join some group, as shown above.
The `group_add` works like in a Django Channels consumer.

See `Channels Layers documentation <https://channels.readthedocs.io/en/stable/topics/channel_layers.html>`_
for information on how to send messages to groups and general consumer functionality.

Runtime State
-------------

A `ContextChannel` can have a runtime state, which is a dictionary in the python
class that is automatically relayed to the frontend. The runtime state persists
for one websocket connection.

Declare the RuntimeState class, extending TypedDict, to create a runtime state.
The TypedDict is required so that proper typescript interfaces can be generated:

.. code-block:: python

    from typing import TypedDict
    from rxdjango.channels import ContextChannel

    class MyChannel(ContextChannel):

        class RuntimeState(TypedDict):
            some_number_var: int
            some_bool_var: bool

The runtime state is accessible as `self.runtime_state` in the ContextChannel.
To change the runtime state, use the `set_runtime_var`. In the example below,
a consumer is used to change a runtime variable.

.. code-block:: python

    from typing import TypedDict
    from rxdjango.channels import ContextChannel

    class MyChannel(ContextChannel):

        class RuntimeState(TypedDict):
            notifications: int

        @consumer('new.notification')
        def relay_notification(self, event):
            notifications = self.runtime_state['notifications']
            self.set_runtime_var('notifications', notifications + 1)

On the frontend side, `runtimeState` is one more key returned by
`useChannelState`. You also need to import and provide the type of
the runtime state:

.. code-block:: typescript

    import { useChannelState } from "@rxdjango/react";
    import { MyNestedType } from "my-rx-frontend-dir/myapp.interfaces";
    import { MyContextChannel, MyContextChannelRuntimeState } from "my-rx-frontend-dir/myapp.channels";

    const MyPage = () => {
      const channel = new MyContextChannel(instanceId, auth.token);
      const { state, runtimeState } = useChannelState<MyNestedType, MyContextChannelRuntimeState>(channel);
    }
