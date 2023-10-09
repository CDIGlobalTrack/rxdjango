RxDjango
=========

RxDjango is a layer over Django Channels aimed to make it as simple as possible to broadcast
changes in Django models to browsers through websockets, with minimal latency.

It's evolving in production for more than 2 years now and it's revised API has just been released
in Python Nordeste 2023. There's no stable release yet.

Quickstart
==========

Start by defining a StateChannel and define an "anchor", which is a serializer that will build
the state that will be sent to user.

```python
# chat/channels.py
from rxdjango.channels import StateChannel
from chat.serializers import ChatRoomSerializer


class ChatRoomChannel(StateChannel):

    class Meta:
        anchor = ChatRoomSerializer()

    def has_permission(self, user, chat_room):
        # check permission
        return True

```

Then define paths for the channels in websocket_urlpatterns.
[Django Channels documentation](https://channels.readthedocs.io/en/latest/tutorial/part_2.html)
suggests you put this in app/routing.py.

```python
# chat/routing.py
from chat.channels import ChatRoomChannel

websocket_urlpatterns = [
    path('ws/chat/<str:room_name>/', ChatRoomChannel.as_asgi()),
]
```

This is all the code it takes in the app! From that, RxDjango will generate all the frontend
code required to keep state in sync between backend and frontend. For that, we need to configure
settings.py with some information about the frontend.

```python
# settings.py

RX_FRONTEND_DIR = os.path.join(BASE_DIR, '../frontend/src/app/modules')
RX_WEBSOCKET_URL = "import.meta.env.VITE_SOCKET_URL"
```

In the above example, we want our code to be generated at src/app/modules, and we take the websocket url from Vite.
Also, there are some backend configuration required. Right now, we only support Redis and Mongo for caching.

```python
# settings.py
MONGO_URL = "configure me"
REDIS_URL = "configure me"
# for now framework expects this to exist, and to be set to True during tests
TESTING = False
```

And, of course, add `rxdjango` to your INSTALLED_APPS

```python
# settings.py
INSTALLED_APPS = [
    # ...
    'rxdjango',
]
```

Build all the frontend files:

```bash
python manage.py makefrontend
```

Check the files generated inside your modules app. Now, use the state
from the backend with:

```typescript
import { ChatRoomChannel } from 'app/modules/chat.channels';
import { useChannelState } from 'django-react';

channel = new ChatRoomChannel(roomName, token);
chatState = useChannelState(channel);
```

That's all it takes! Note that token is a rest_framework.authtoken token,
the only authentication method supported for now.
