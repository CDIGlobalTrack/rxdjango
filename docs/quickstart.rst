
.. _quickstart:

==========
Quickstart
==========

This quickstart assumes you have a Django and React application,
and on Django side you already have a serializer.

Install RxDjango
================

Start by installing RxDjango

.. code-block:: bash

    pip install rxdjango


RxDjango depends on daphne and channels. Add all these to INSTALLED_APPS,
make sure `rxdjango` comes before `daphne`, and both come before
`django.contrib.staticfiles`.

.. code-block:: python

    INSTALLED_APPS = [
        # rxdjango must come before daphne, and both before contrib.staticfiles

        'rxdjango',
        'daphne',
        'django.contrib.staticfiles',

        # these can come anywhere
        'channels',
    ]

Configure ASGI and Redis
========================

Set the ASGI_APPLICATION variable

.. code-block:: python

    ASGI_APPLICATION = 'your_project.asgi.application'

RxDjango depends on Redis for messaging. Configure REDIS_URL.

.. code-block:: python

    REDIS_URL = f'redis://127.0.0.1:6379/0'

Configure MongoDB for Caching
=============================

RxDjango comes with a native cache system using MongoDB.

.. code-block:: python

    MONGO_URL = 'mongodb://localhost:27017/'
    MONGO_STATE_DB = 'hot_state'

Configure Frontend Code Generation
==================================

RxDjango automatically generates **Typescript interfaces and classes for the
frontend** based on the backend classes. You need to configure a directory in
your frontend code and the websocket url of your application.

.. code-block:: python

    RX_FRONTEND_DIR = os.path.join(BASE_DIR, '../frontend/src/app/modules')
    RX_WEBSOCKET_URL = "http://localhost:8000/ws"

Create a ContextChannel
=======================

This quickstart assumes you already have models and
`serializers.ModelSerializer` class, most likely a nested
serializer.

Create a channels.py file, and create a `rxdjango.channels.ContextChannels`
subclass.

.. code-block:: python

    from rxdjango.channels import ContextChannel
    from myapp.serializers import MyNestedSerializer


    class MyContextChannel(ContextChannel):

        class Meta:
            state = MyNestedSerializer()

        @staticmethod
        def has_permission(self, user, instance):
            # check if user has permission on instance
            return True

Set Up WebSocket Routing
========================

Create a route for this channel in asgi.py:

.. code-block:: python

    from myapp.channels import MyContextChannel

    websocket_urlpatterns = [
        path('ws/myapp/<str:mymodel_id>/', MyContextChannel.as_asgi()),
    ]

    application = ProtocolTypeRouter({
        "http": app,
        "websocket": URLRouter(
            websocket_urlpatterns
        ),
    })

Generate Frontend Files
=======================

Run the makefrontend command to generate interfaces matching
your serializer and a MyContextChannel class in the frontend.

.. code-block:: bash

    python manage.py makefrontend

Alternatively, you can pass --makefrontend option to runserver command
during development, so frontend files are automatically generated on
changes.

.. code-block:: bash

    python manage.py runserver --makefrontend

Check the files generated inside your modules app. There are interfaces
matching your serializer, and a `MyContextChannel` class on the frontend
which provides an API to access the backend ContextChannel.

Install RxDjango on the Frontend
================================

RxDjango provides the `@rxdjango/react` library for frontend integration.
Install it using or your preferred package manager, this example uses `yarn`:


.. code-block:: bash

    yarn add @rxdjango/react

Connect the Frontend State
==========================

On your frontend code, link the state of your page with MyContextChannel.
The token variable is the token from `rest_framework.authtoken.models.Token`,
the only supported authentication method for now.

.. code-block:: typescript

    import { MyContextChannel } from 'app/modules/myapp.channels';
    import { useChannelState } from '@rxdjango/react';

    const channel = new MyContextChannel(mymodelId, token);
    const state = useChannelState(channel);


That's it! Now:

* The state variable in the frontend automatically updates when the database changes.
* Data is serialized and cached as flat dictionaries.
* Signals broadcast updates to connected clients and MongoDB cache.
* The nested instance is reconstructed on the frontend.

For the signals to work, make sure you use `instance.save()`, live updates
won't work if you use `YourModel.objects.update()`.
