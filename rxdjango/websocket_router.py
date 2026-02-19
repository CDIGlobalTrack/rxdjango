"""WebSocket subscription management and message routing.

This module handles routing real-time updates to connected WebSocket clients
via Django Channels' group layer. Each ContextChannel has a WebsocketRouter
that manages channel group subscriptions and dispatches serialized instance
deltas to the correct clients.

Clients are organized into two types of channel groups:

- **Anchor groups** (``{name}_{anchor_id}``): All clients subscribed to a
  particular anchor receive shared (non-user-specific) updates.
- **User groups** (``{name}_{anchor_id}_{user_id}``): User-specific updates
  (filtered by ``user_key`` on the serializer) are sent only to the matching
  user's group.

A global system channel is also available for broadcasting administrative
messages to all connected clients.
"""

import json
from asgiref.sync import async_to_sync
import channels.layers
from rxdjango.serialize import json_dumps
from django.conf import settings


SYSTEM_CHANNEL = getattr(
    settings, 'RXDJANGO_SYSTEM_CHANNEL', '_rxdjango_system'
)


def get_channel_key(name, anchor_id, user_id=None):
    """Build a channel group key for routing messages.

    Args:
        name: The channel class name (lowercased).
        anchor_id: The root object ID scoping the subscription.
        user_id: Optional user ID. When provided, produces a user-specific
            group key for ``user_key``-filtered instance delivery.

    Returns:
        str: A group key like ``"mychannel_42"`` or ``"mychannel_42_7"``.
    """
    if user_id is None:
        return f'{name}_{anchor_id}'
    return f'{name}_{anchor_id}_{user_id}'


async def send_system_message(source, message):
    """Broadcast a system message to all connected WebSocket clients.

    Sends a message to the global system channel group, which every
    connected consumer joins on connect.

    Args:
        source: Identifier for the message source (e.g., management command name).
        message: The message payload to broadcast.
    """
    channel_layer = channels.layers.get_channel_layer()
    payload = {
        'source': source,
        'message': message,
    }
    await channel_layer.group_send(
        SYSTEM_CHANNEL,
        {
            'type': 'relay',
            'payload': payload,
        },
    )


class WebsocketRouter:
    """Routes real-time instance updates to subscribed WebSocket clients.

    Each ContextChannel has one WebsocketRouter instance, created during
    metaclass initialization. The router manages channel group memberships
    and dispatches serialized deltas to the appropriate groups via Django
    Channels' group layer.

    Attributes:
        name: The lowercased channel class name, used as the group key prefix.
    """

    def __init__(self, name):
        """Initialize the router.

        Args:
            name: The lowercased channel class name used to build group keys.
        """
        self.name = name

    async def connect(self, channel_layer, channel_name, anchor_id, user_id):
        """Subscribe a consumer to its anchor, user, and system groups.

        Called when a WebSocket client connects. Adds the consumer to three
        channel groups:

        1. The anchor group (receives shared updates for this anchor).
        2. The user-specific group (receives user-scoped updates).
        3. The global system channel (receives admin broadcasts).

        Args:
            channel_layer: The Django Channels layer for group operations.
            channel_name: The unique channel name for this consumer instance.
            anchor_id: The root object ID scoping the subscription.
            user_id: The authenticated user's ID.
        """
        # Join room group
        await channel_layer.group_add(
            get_channel_key(self.name, anchor_id),
            channel_name,
        )

        # Join this user room group, so that user-specific
        # data is shared
        await channel_layer.group_add(
            get_channel_key(self.name, anchor_id, user_id),
            channel_name,
        )

        # Connect to the system channel
        await channel_layer.group_add(SYSTEM_CHANNEL, channel_name)

    def sync_dispatch(self, payload, anchor_id, user_id=None):
        """Synchronous wrapper around :meth:`dispatch`.

        Used by signal handlers running in Django's synchronous context
        to dispatch updates without an active event loop.

        Args:
            payload: The serialized instance data or delta to broadcast.
            anchor_id: The anchor ID identifying the target group.
            user_id: Optional user ID for user-scoped delivery.
        """
        async_to_sync(self.dispatch)(payload, anchor_id, user_id)

    async def dispatch(self, payload, anchor_id, user_id=None):
        """Send a payload to the appropriate channel group.

        The payload is round-tripped through JSON serialization to ensure
        all values (e.g., datetimes, Decimals) are JSON-safe before being
        passed to the channel layer.

        When ``user_id`` is provided, the payload is sent to the user-specific
        group; otherwise it goes to the shared anchor group.

        Args:
            payload: The serialized instance data or delta to broadcast.
            anchor_id: The anchor ID identifying the target group.
            user_id: Optional user ID for user-scoped delivery.
        """
        channel_key = get_channel_key(self.name, anchor_id, user_id)
        channel_layer = channels.layers.get_channel_layer()

        # FIXME: Datetime fields should be handled prior to this
        payload = json_dumps(payload)
        payload = json.loads(payload)

        await channel_layer.group_send(
            channel_key,
            {
                'type': 'relay',
                'payload': payload,
            },
        )

    async def disconnect(self, channel_layer, channel_name, anchor_id, user_id=None):
        """Unsubscribe a consumer from all its channel groups.

        Called when a WebSocket client disconnects. Removes the consumer
        from the anchor group, the user-specific group (if applicable),
        and the global system channel.

        Args:
            channel_layer: The Django Channels layer for group operations.
            channel_name: The unique channel name for this consumer instance.
            anchor_id: The root object ID scoping the subscription.
            user_id: Optional user ID. If provided, also leaves the
                user-specific group.
        """
        # Leave the room group
        await channel_layer.group_discard(
            get_channel_key(self.name, anchor_id),
            channel_name,
        )

        # Leave this user room group
        if user_id is not None:
            await channel_layer.group_discard(
                get_channel_key(self.name, anchor_id, user_id),
                channel_name,
            )

        # Leave the system channel
        await channel_layer.group_discard(SYSTEM_CHANNEL, channel_name)
