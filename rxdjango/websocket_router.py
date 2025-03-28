import json
from asgiref.sync import async_to_sync
import channels.layers
from rxdjango.serialize import json_dumps
from django.conf import settings


SYSTEM_CHANNEL = getattr(
    settings, 'RXDJANGO_SYSTEM_CHANNEL', '_rxdjango_system'
)


def get_channel_key(name, anchor_id, user_id=None):
    if user_id is None:
        return f'{name}_{anchor_id}'
    return f'{name}_{anchor_id}_{user_id}'


async def send_system_message(source, message):
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

    def __init__(self, name):
        self.name = name

    async def connect(self, channel_layer, channel_name, anchor_id, user_id):
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
        async_to_sync(self.dispatch)(payload, anchor_id, user_id)

    async def dispatch(self, payload, anchor_id, user_id=None):
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
