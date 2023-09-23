import json
from asgiref.sync import async_to_sync
import channels.layers

def get_channel_key(name, anchor_id, user_id=None):
    if user_id is None:
        return f'{name}_{anchor_id}'
    return f'{name}_{anchor_id}_{user_id}'


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

    def sync_dispatch(self, payload, anchor_id, user_id=None):
        async_to_sync(self.dispatch)(payload, anchor_id, user_id)

    async def dispatch(self, payload, anchor_id, user_id=None):
        channel_key = get_channel_key(self.name, anchor_id, user_id)
        channel_layer = channels.layers.get_channel_layer()

        # FIXME: Datetime fields should be handled prior to this
        # This is probably due to properties such as launch_time
        payload = json.dumps(payload, default=str)
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
