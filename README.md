Django Websockets Framework
===========================

Django Websockets Framework (DWF) is a layer over Django Channels aimed to make it as simple as possible to broadcast
changes in Django models to browsers through websockets, with minimal latency.

It's been tested in production for 18 months and is now being released as v0.1. The current released code is not functional yet,
as it needs some adjustments to become a proper Open Source project. The current code was released at Python Brasil 2022, as a
first step to build a community around it.

Architecture
============

DWF uses a snapshot/updates model: once browser connects to the websocket endpoint, a snapshot containing all the relevant instances
serialized will be sent, and subsequent updates will be broadcast to all connected clients.

Example
=======

```python
# chat/channels.py
class ChatRoomChannel(ModelSetChannel):
    snapshot = ChatRoomSnapshotSerializer

    pieces = [
        ChatRoomSerializer,
        OnlineUserSerializer,
        MessageSerializer,
    ]

    def has_permission(self, user, survey_id):
        # check permission
        return True


# chat/signals.py
# automatically broadcast any change
channel = ChatRoomChannel()
channel.broadcast_updates(ChatRoom)
channel.broadcast_updates(OnlineUser)

# chat/models.py
class Message(models.Model):
    ...
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # or trigger brodcast manually
        from chat.channels import ChatRoomChannel
        channel = ChatRoomChannel()
        channel.broadcast_instance(self.chatroom, self)

```

Features
========

* Automatically implement signals to broadcast changes
* Send snapshot through a HTTP request, synchronized with socket, to make use of browser caching
* Store indexed transactions in mongodb, so that user can get lost updates on reconnection
