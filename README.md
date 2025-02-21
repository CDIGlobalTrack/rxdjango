RxDjango
========

**Seamless integration between Django and React**

RxDjango is a layer over Django Channels and Django REST Framework aimed
to make it as simple as possible to integrate backend and frontend, with
performance and minimal latency. On the frontend side, it supports
the React framework now, and adapters to other frontend frameworks can
be implemented.


Quickstart
==========

This quickstart assumes you have a Django and React application,
and on Django side you already have a serializer.

Start by installing RxDjango

   ```bash
   pip install rxdjango
   ```

RxDjango depends on daphne and channels. Add all these to INSTALLED_APPS,
make sure `rxdjango` comes before `daphne`, and both come before
`django.contrib.staticfiles`.

   ```python
   INSTALLED_APPS = [
       # rxdjango must come before daphne, and both before contrib.staticfiles

       'rxdjango',
       'daphne',
       'django.contrib.staticfiles',

       # these can come anywhere
       'channels',
   ]
   ```

Set the ASGI_APPLICATION variable

   ```python
   ASGI_APPLICATION = 'your_project.asgi.application'
   ```

RxDjango depends on Redis for messaging. Configure REDIS_URL.

   ```python
   REDIS_URL = f'redis://127.0.0.1:6379/0'
   ```

RxDjango comes with a native cache system using MongoDB.

   ```python
   MONGO_URL = 'mongodb://localhost:27017/'
   MONGO_STATE_DB = 'hot_state'
   ```

Typescript interfaces and classes for the frontend to communicate with
backend will automatically be generated. For that, you need to configure
a directory in your frontend code and the websocket url of your application.

   ```python
   RX_FRONTEND_DIR = os.path.join(BASE_DIR, '../frontend/src/app/modules')
   RX_WEBSOCKET_URL = "http://localhost:8000/ws"
   ```

This quickstart assumes you already have models and
`serializers.ModelSerializer` class, most likely a nested
serializer.

Create a channels.py file, and create a `rxdjango.channels.ContextChannels`
subclass.

   ```python
   from rxdjango.channels import ContextChannel
   from myapp.serializers import MyNestedSerializer


   class MyContextChannel(ContextChannel):

       class Meta:
           state = MyNestedSerializer()

       def has_permission(self, user, instance):
           # check if user has permission on instance
           return True

   ```

Create a route for this channel in asgi/routing.py:

   ```python
   from myapp.channels import MyContextChannel
   
   websocket_urlpatterns = [
       path('ws/myapp/<str:mymodel_id>/', MyContextChannel.as_asgi()),
   ]
   ```

Now run the makefrontend command. It will generate interfaces matching
your serializer and a MyContextChannel class in the frontend, with
an interface to access the backend.

   ```bash
   python manage.py makefrontend
   ```

Alternatively, you can pass --makefrontend option to runserver command
during development, so frontend files are automatically generated on
changes.

   ```bash
   python manage.py runserver --makefrontend
   ```

Check the files generated inside your modules app. There are interfaces
matching your serializer, and a `MyContextChannel` class on the frontend.

You need to install `@rxdjango/react` on the frontend. In this example we'll
use yarn, use whichever package manager of you choice:

  ```bash
  yarn add @rxdjango/react
  ```

On your frontend code, link the state of your page with MyContextChannel.
The token variable is the token from `rest_framework.authtoken.models.Token`,
the only supported authentication method for now.

   ```typescript
   import { MyContextChannel } from 'app/modules/myapp.channels';
   import { useChannelState } from '@rxdjango/react';

   const channel = new MyContextChannel(mymodelId, token);
   const state = useChannelState(channel);
```

That's all it takes, now the state will hold the serialized instance as if
done by your nested serializer, and any updates in the database
will update your state automatically.

Internally, instances are serialized and cached as flat dictionaries,
and signals are used to broadcast instances to clients and cache.
The full nested instance is rebuilt on client side for performance.
For the signals to work, make sure you use `instance.save()`, live updates
won't work if you use `YourModel.objects.update()`.

Full documentation, that details API and explain channels with multiple
instances is on the way. 
