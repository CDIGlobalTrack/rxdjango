import json
from django.utils import timezone
from django.db import models
from asgiref.sync import sync_to_async, async_to_sync
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from rest_framework.authtoken.models import Token
from account.tasks import update_last_login
from funnels.models import UserSession
from wsframework.cache import get_cached, set_cached
from wsframework.views import create_snapshot_link
from wsframework.mongo import get_latest_transaction_id, get_updates
from wsframework.exceptions import ProgrammingError


class ModelSetConsumer(AsyncWebsocketConsumer):

    def get_base_url(self):
        protocol = host = None
        for key, val in self.scope['headers']:
            key = key.decode()
            if key == 'host':
                host = val.decode()
            elif key == 'origin':
                protocol = val.decode().split('//')[0]
            if host and protocol:
                break

        return f'{protocol}//{host}'

    @property
    def user_channel(self):
        if not self.user:
            return
        return f'{self.channel_group_name}_{self.user.id}'

    async def connect(self):
        kwargs = self.scope['url_route']['kwargs']

        self.channel = self.model_set_channel()
        self.channel_instance_id = int(kwargs['channel_instance_id'])
        self.channel_group_name = f'{self.channel.key}_{self.channel_instance_id}'
        self.base_url = self.get_base_url()

        self.user = None
        self.token = None

        await self.accept()

    @database_sync_to_async
    def serialized_data(self, Serializer, data):
        return Serializer(data).data

    async def start(self, transaction=None):
        # Join room group
        await self.channel_layer.group_add(
            self.channel_group_name,
            self.channel_name,
        )

        # Join this user room group, so that user-specific
        # data is shared
        await self.channel_layer.group_add(
            self.user_channel,
            self.channel_name,
        )

        if transaction:
            await self.send_updates_since(transaction)
        else:
            transaction = 0
            await self.send_snapshot()

        await self.channel.initialize(self.user,
                                      self.channel_instance_id,
                                      transaction > 0)

    async def disconnect(self, close_code):
        # Leave room groups
        await self.channel_layer.group_discard(
            self.channel_group_name,
            self.channel_name
        )
        await self.channel_layer.group_discard(
            self.user_channel,
            self.channel_name
        )

        await self.channel.finish(self.user, self.channel_instance_id)
        await self.terminate_session()

    @database_sync_to_async
    def get_snapshot(self):
        channel = self.channel

        Model = channel.model

        cache_key = None
        data = None
        last_modified = None

        if channel.snapshot_cache_key:
            prefix = channel.snapshot_cache_key
            pk = self.channel_instance_id
            cache_key = f'{prefix}-{pk}'

        if cache_key:
            cached = get_cached(cache_key)
            if cached:
                data = cached['data']
                last_modified = cached['last_modified']

        if not data:
            try:
                instance = Model.objects.get(id=self.channel_instance_id)
            except Model.DoesNotExist:
                return None, None, None, None
            Serializer = channel.snapshot
            data = Serializer(instance).data
            last_modified = set_cached(cache_key, data)

        data = async_to_sync(channel.initialize_snapshot)(data)

        return (Model.__name__,
                self.channel_instance_id,
                data,
                last_modified)


    async def send_snapshot(self, refresh=False):
        result = await self.get_snapshot()
        instance_type, instance_id, data, last_modified = result
        snapshot = {
            'operation': 'snapshot',
            'instance_type': instance_type,
            'instance_id': instance_id,
            'data': data,
        }
        transaction = get_latest_transaction_id(self.channel.key,
                                                self.channel_instance_id)

        create_link = sync_to_async(create_snapshot_link)
        cache_key = f'{self.channel.snapshot_cache_key}-{instance_id}'

        url = await create_link(self.token, cache_key, snapshot, last_modified)
        payload = [{
            'operation': 'url',
            'url': f'{self.base_url}{url}',
        }]

        payload += await self.get_snapshot_complement()

        payload = {
            'payload': payload,
            'transaction': transaction,
        }
        data = json.dumps(payload)
        await self.send(text_data=data)


    async def get_snapshot_complement(self):
        pieces = self.channel.complement_snapshot(self.user,
                                                  self.channel_instance_id)
        updates = []
        async for piece in pieces:
            if isinstance(piece, dict):
                if 'channel_instance_id' in piece:
                    del piece['channel_instance_id']
                if 'transaction' in piece:
                    del piece['transaction']

                piece['operation'] = 'create'
                updates.append(piece)
            elif isinstance(piece, models.Model):
                Serializer = self.channel.get_serializer(piece)
                data = await self.serialized_data(Serializer, piece)
                updates.append(dict(
                    operation='snapshot_part',
                    instance_type=piece.__class__.__name__,
                    instance_id=piece.id,
                    data=data,
                    ))
            else:
                raise ProgrammingError("Snapshot part must be either dict or models.Model instance")

        return updates

    async def send_updates_since(self, client_transaction):
        updates = get_updates(self.channel.key,
                              self.channel_instance_id,
                              client_transaction)

        for update in updates:
            del update['transaction']

        transaction = get_latest_transaction_id(self.channel.key,
                                                self.channel_instance_id)

        data = json.dumps({
            'payload': updates,
            'transaction': transaction,
        })
        await self.send(text_data=data)

    async def receive(self, text_data):
        if not self.user:
            data = text_data.split()
            token = data[0]
            try:
                transaction = int(data[1])
            except IndexError:
                transaction = None
            self.user = await self.authenticate(token=token)
            if self.user:
                await self.start(transaction)
            else:
                await self.close()

    @database_sync_to_async
    def authenticate(self, token):
        try:
            token = Token.objects.get(key=token)
        except Token.DoesNotExist:
            return
        self.session = UserSession.objects.create(
            user=token.user,
            type=self.channel.key,
            instance_id=self.channel_instance_id,
            )
        self.token = token.key
        update_last_login.delay(token.user.id, timezone.now())

        if self.channel.has_permission(token.user,
                                       self.channel_instance_id):
            return token.user

    @database_sync_to_async
    def terminate_session(self):
        try:
            self.session.terminate()
        except AttributeError:  # User have not authenticated
            pass

    async def broadcast(self, payload):
        payload = payload['payload']
        await self.send(text_data=json.dumps(payload, default=str))
