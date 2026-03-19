"""
End-to-end tests for optimistic write operations (save, create, delete).

These tests verify the full write operation lifecycle:
1. Client sends write message with optimistic update data
2. Server validates via can_save/can_create/can_delete
3. Server executes database operation
4. Server broadcasts canonical state to all clients
5. Client receives writeResponse with success/failure
"""
import json
import asyncio
from channels.testing import WebsocketCommunicator
from channels.routing import URLRouter
from channels.db import database_sync_to_async
from django.test import TestCase, TransactionTestCase
from django.urls import path
from rest_framework.authtoken.models import Token
from users.models import User as AuthUser
from react_test.models import User, Project, Participant, Job, Asset, Deadline, Task, Task
from react_test.channels import JobContextChannel
from rxdjango.mongo import MongoSignalWriter
from rxdjango.redis import RedisSession
from rxdjango.transaction_manager import TransactionBroadcastManager

T = 1


def make_key(instance):
    _type = instance.get('_instance_type', '')
    if not _type:
        return None
    return f"{_type}:{instance['id']}"


websocket_urlpatterns = [
    path('ws/job/<int:job_id>/', JobContextChannel.as_asgi()),
]


class WriteOperationsTestCase(TransactionTestCase):
    """Test write operations (save, create, delete) via WebSocket."""

    def get_ws(self, job_id):
        application = URLRouter(websocket_urlpatterns)
        return WebsocketCommunicator(application, f'/ws/job/{job_id}/')

    def setUp(self):
        self.auth_user = AuthUser.objects.create_user(
            login='writeuser',
            password='writepass',
        )
        self.token = Token.objects.create(user=self.auth_user)
        self.auth = json.dumps({'token': self.token.key})

        user1 = User.objects.create(name="Developer")
        project = Project.objects.create(name="Write Test Project")
        self.participant = Participant.objects.create(
            project=project,
            user=user1,
            name="Test Participant",
            role="Developer",
        )
        self.job = Job.objects.create(project=project, name="Write Test Job")
        self.task = self.job.tasks.create(name="Initial Task", developer=self.participant)
        self.asset = self.job.asset_set.create(name="Initial Asset")

        TransactionBroadcastManager._clear()
        RedisSession.init_database(JobContextChannel)
        MongoSignalWriter(JobContextChannel).init_database()

    async def safe_disconnect(self, ws):
        try:
            await ws.disconnect()
        except asyncio.CancelledError:
            pass

    async def connect_and_auth(self):
        ws = self.get_ws(self.job.id)
        connected, _ = await ws.connect()
        assert connected
        await ws.send_to(text_data=self.auth)
        return ws

    async def receive_state(self, ws):
        index = {}
        while True:
            try:
                response = await ws.receive_output(timeout=T)
                data = json.loads(response['text'])
                if isinstance(data, dict):
                    continue
                for instance in data:
                    key = make_key(instance)
                    if key is None:
                        return index
                    index[key] = instance
            except asyncio.TimeoutError:
                return index

    async def receive_updates(self, ws):
        index = {}
        while True:
            try:
                response = await ws.receive_output(timeout=T)
                data = json.loads(response['text'])
                if isinstance(data, list):
                    for instance in data:
                        key = make_key(instance)
                        if key:
                            index[key] = instance
                elif isinstance(data, dict) and '_instance_type' in data:
                    key = make_key(data)
                    if key:
                        index[key] = data
            except asyncio.TimeoutError:
                return index

    async def send_write(self, ws, write_id, operation, **kwargs):
        msg = {
            'type': 'write',
            'writeId': write_id,
            'operation': operation,
            **kwargs,
        }
        await ws.send_to(text_data=json.dumps(msg))

    async def receive_write_response(self, ws, write_id):
        while True:
            try:
                response = await ws.receive_output(timeout=T)
                data = json.loads(response['text'])
                if isinstance(data, dict) and data.get('type') == 'writeResponse':
                    if data.get('writeId') == write_id:
                        return data
            except asyncio.TimeoutError:
                return None

    async def test_save_operation_updates_instance(self):
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        write_id = 1001
        await self.send_write(
            ws,
            write_id,
            'save',
            instanceType='react_test.serializers.TaskSerializer',
            instanceId=self.task.id,
            data={'name': 'Updated Task Name'},
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response)
        self.assertTrue(response.get('success'))

        updates = await self.receive_updates(ws)
        task_key = f'react_test.serializers.TaskSerializer:{self.task.id}'
        self.assertIn(task_key, updates)
        self.assertEqual(updates[task_key]['name'], 'Updated Task Name')

        task = await database_sync_to_async(
            lambda: Task.objects.get(pk=self.task.id)
        )()
        self.assertEqual(task.name, 'Updated Task Name')

        await self.safe_disconnect(ws)

    async def test_save_operation_on_anchor(self):
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        write_id = 1002
        await self.send_write(
            ws,
            write_id,
            'save',
            instanceType='react_test.serializers.JobNestedSerializer',
            instanceId=self.job.id,
            data={'name': 'Updated Job Name'},
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response)
        self.assertTrue(response.get('success'))

        updates = await self.receive_updates(ws)
        job_key = f'react_test.serializers.JobNestedSerializer:{self.job.id}'
        self.assertIn(job_key, updates)
        self.assertEqual(updates[job_key]['name'], 'Updated Job Name')

        job = await database_sync_to_async(
            lambda: Job.objects.get(pk=self.job.id)
        )()
        self.assertEqual(job.name, 'Updated Job Name')

        await self.safe_disconnect(ws)

    async def test_create_operation_adds_child(self):
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        write_id = 1003
        await self.send_write(
            ws,
            write_id,
            'create',
            instanceType='react_test.serializers.TaskSerializer',
            parentType='react_test.serializers.JobNestedSerializer',
            parentId=self.job.id,
            relationName='tasks',
            data={'name': 'New Task Via Write', 'developer': self.participant.id},
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response)
        self.assertTrue(response.get('success'))

        updates = await self.receive_updates(ws)
        new_task = await database_sync_to_async(
            lambda: Task.objects.get(name='New Task Via Write', job=self.job)
        )()
        task_key = f'react_test.serializers.TaskSerializer:{new_task.id}'
        self.assertIn(task_key, updates)
        self.assertEqual(updates[task_key]['name'], 'New Task Via Write')

        await self.safe_disconnect(ws)

    async def test_create_operation_for_asset(self):
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        write_id = 1004
        await self.send_write(
            ws,
            write_id,
            'create',
            instanceType='react_test.serializers.AssetSerializer',
            parentType='react_test.serializers.JobNestedSerializer',
            parentId=self.job.id,
            relationName='asset_set',
            data={'name': 'New Asset Via Write'},
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response)
        self.assertTrue(response.get('success'))

        updates = await self.receive_updates(ws)
        new_asset = await database_sync_to_async(
            lambda: Asset.objects.get(name='New Asset Via Write', job=self.job)
        )()
        asset_key = f'react_test.serializers.AssetSerializer:{new_asset.id}'
        self.assertIn(asset_key, updates)
        self.assertEqual(updates[asset_key]['name'], 'New Asset Via Write')

        await self.safe_disconnect(ws)

    async def test_delete_operation_removes_instance(self):
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        asset_to_delete = await database_sync_to_async(
            lambda: self.job.asset_set.create(name='Asset To Delete')
        )()

        write_id = 1005
        await self.send_write(
            ws,
            write_id,
            'delete',
            instanceType='react_test.serializers.AssetSerializer',
            instanceId=asset_to_delete.id,
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response)
        self.assertTrue(response.get('success'))

        exists = await database_sync_to_async(
            lambda: Asset.objects.filter(pk=asset_to_delete.id).exists()
        )()
        self.assertFalse(exists)

        await self.safe_disconnect(ws)

    async def test_delete_operation_on_task(self):
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        task_to_delete = await database_sync_to_async(
            lambda: self.job.tasks.create(name='Task To Delete', developer=self.participant)
        )()

        write_id = 1006
        await self.send_write(
            ws,
            write_id,
            'delete',
            instanceType='react_test.serializers.TaskSerializer',
            instanceId=task_to_delete.id,
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response)
        self.assertTrue(response.get('success'))

        exists = await database_sync_to_async(
            lambda: Task.objects.filter(pk=task_to_delete.id).exists()
        )()
        self.assertFalse(exists)

        await self.safe_disconnect(ws)

    async def test_save_returns_error_for_unknown_instance_type(self):
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        write_id = 1007
        await self.send_write(
            ws,
            write_id,
            'save',
            instanceType='unknown.serializer.UnknownSerializer',
            instanceId=999,
            data={'name': 'Test'},
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response)
        self.assertFalse(response.get('success'))
        self.assertIn('error', response)
        self.assertEqual(response['error']['code'], 400)

        await self.safe_disconnect(ws)

    async def test_save_returns_error_for_nonexistent_instance(self):
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        write_id = 1008
        await self.send_write(
            ws,
            write_id,
            'save',
            instanceType='react_test.serializers.TaskSerializer',
            instanceId=99999,
            data={'name': 'Test'},
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response)
        self.assertFalse(response.get('success'))
        self.assertIn('error', response)
        self.assertEqual(response['error']['code'], 400)

        await self.safe_disconnect(ws)

    async def test_delete_returns_error_for_nonexistent_instance(self):
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        write_id = 1009
        await self.send_write(
            ws,
            write_id,
            'delete',
            instanceType='react_test.serializers.AssetSerializer',
            instanceId=99999,
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response)
        self.assertFalse(response.get('success'))
        self.assertIn('error', response)
        self.assertEqual(response['error']['code'], 400)

        await self.safe_disconnect(ws)

    async def test_create_returns_error_for_unknown_parent(self):
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        write_id = 1010
        await self.send_write(
            ws,
            write_id,
            'create',
            instanceType='react_test.serializers.TaskSerializer',
            parentType='react_test.serializers.JobNestedSerializer',
            parentId=99999,
            relationName='tasks',
            data={'name': 'Test Task'},
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response)
        self.assertFalse(response.get('success'))
        self.assertIn('error', response)
        self.assertEqual(response['error']['code'], 400)

        await self.safe_disconnect(ws)

    async def test_save_with_foreign_key_update(self):
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        new_participant = await database_sync_to_async(
            lambda: Participant.objects.create(
                project=self.job.project,
                user=User.objects.create(name="New Developer"),
                name="New Participant",
                role="QA",
            )
        )()

        write_id = 1011
        await self.send_write(
            ws,
            write_id,
            'save',
            instanceType='react_test.serializers.TaskSerializer',
            instanceId=self.task.id,
            data={'developer': new_participant.id},
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response)
        self.assertTrue(response.get('success'))

        task = await database_sync_to_async(
            lambda: Task.objects.get(pk=self.task.id)
        )()
        self.assertEqual(task.developer_id, new_participant.id)

        await self.safe_disconnect(ws)

    async def test_multiple_concurrent_writes(self):
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        write_ids = [2001, 2002, 2003]
        for i, write_id in enumerate(write_ids):
            await self.send_write(
                ws,
                write_id,
                'create',
                instanceType='react_test.serializers.AssetSerializer',
                parentType='react_test.serializers.JobNestedSerializer',
                parentId=self.job.id,
                relationName='asset_set',
                data={'name': f'Concurrent Asset {i}'},
            )

        responses = {}
        for write_id in write_ids:
            response = await self.receive_write_response(ws, write_id)
            responses[write_id] = response

        for write_id in write_ids:
            self.assertIsNotNone(responses[write_id])
            self.assertTrue(responses[write_id].get('success'))

        count = await database_sync_to_async(
            lambda: self.job.asset_set.filter(name__startswith='Concurrent Asset').count()
        )()
        self.assertEqual(count, 3)

        await self.safe_disconnect(ws)


class WriteAuthorizationTestCase(TransactionTestCase):
    """Test authorization via can_save/can_create/can_delete methods."""

    def get_ws(self, job_id):
        application = URLRouter(websocket_urlpatterns)
        return WebsocketCommunicator(application, f'/ws/job/{job_id}/')

    def setUp(self):
        self.auth_user = AuthUser.objects.create_user(
            login='authuser',
            password='authpass',
        )
        self.token = Token.objects.create(user=self.auth_user)
        self.auth = json.dumps({'token': self.token.key})

        user1 = User.objects.create(name="Developer")
        project = Project.objects.create(name="Auth Test Project")
        self.participant = Participant.objects.create(
            project=project,
            user=user1,
            name="Auth Participant",
            role="Developer",
        )
        self.job = Job.objects.create(project=project, name="Auth Test Job")
        self.task = self.job.tasks.create(name="Auth Task", developer=self.participant)

        TransactionBroadcastManager._clear()
        RedisSession.init_database(JobContextChannel)
        MongoSignalWriter(JobContextChannel).init_database()

    async def safe_disconnect(self, ws):
        try:
            await ws.disconnect()
        except asyncio.CancelledError:
            pass

    async def connect_and_auth(self):
        ws = self.get_ws(self.job.id)
        connected, _ = await ws.connect()
        assert connected
        await ws.send_to(text_data=self.auth)
        return ws

    async def receive_state(self, ws):
        index = {}
        while True:
            try:
                response = await ws.receive_output(timeout=T)
                data = json.loads(response['text'])
                if isinstance(data, dict):
                    continue
                for instance in data:
                    key = make_key(instance)
                    if key is None:
                        return index
                    index[key] = instance
            except asyncio.TimeoutError:
                return index

    async def send_write(self, ws, write_id, operation, **kwargs):
        msg = {
            'type': 'write',
            'writeId': write_id,
            'operation': operation,
            **kwargs,
        }
        await ws.send_to(text_data=json.dumps(msg))

    async def receive_write_response(self, ws, write_id):
        while True:
            try:
                response = await ws.receive_output(timeout=T)
                data = json.loads(response['text'])
                if isinstance(data, dict) and data.get('type') == 'writeResponse':
                    if data.get('writeId') == write_id:
                        return data
            except asyncio.TimeoutError:
                return None

    async def test_can_save_allows_write(self):
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        write_id = 3001
        await self.send_write(
            ws,
            write_id,
            'save',
            instanceType='react_test.serializers.TaskSerializer',
            instanceId=self.task.id,
            data={'name': 'Authorized Update'},
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response)
        self.assertTrue(response.get('success'))

        await self.safe_disconnect(ws)

    async def test_can_create_allows_write(self):
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        write_id = 3002
        await self.send_write(
            ws,
            write_id,
            'create',
            instanceType='react_test.serializers.TaskSerializer',
            parentType='react_test.serializers.JobNestedSerializer',
            parentId=self.job.id,
            relationName='tasks',
            data={'name': 'Authorized Create', 'developer': self.participant.id},
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response)
        self.assertTrue(response.get('success'))

        await self.safe_disconnect(ws)

    async def test_can_delete_allows_write(self):
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        task_to_delete = await database_sync_to_async(
            lambda: self.job.tasks.create(name='Task To Delete', developer=self.participant)
        )()

        write_id = 3003
        await self.send_write(
            ws,
            write_id,
            'delete',
            instanceType='react_test.serializers.TaskSerializer',
            instanceId=task_to_delete.id,
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response)
        self.assertTrue(response.get('success'))

        await self.safe_disconnect(ws)


class WriteForbiddenTestCase(TransactionTestCase):
    """Test that forbidden operations are properly rejected."""

    def get_ws(self, job_id):
        application = URLRouter(websocket_urlpatterns)
        return WebsocketCommunicator(application, f'/ws/job/{job_id}/')

    def setUp(self):
        self.auth_user = AuthUser.objects.create_user(
            login='forbiddenuser',
            password='forbiddenpass',
        )
        self.token = Token.objects.create(user=self.auth_user)
        self.auth = json.dumps({'token': self.token.key})

        user1 = User.objects.create(name="Forbidden Developer")
        project = Project.objects.create(name="Forbidden Test Project")
        self.participant = Participant.objects.create(
            project=project,
            user=user1,
            name="Forbidden Participant",
            role="Developer",
        )
        self.job = Job.objects.create(project=project, name="Forbidden Test Job")
        self.task = self.job.tasks.create(name="Forbidden Task", developer=self.participant)
        self.protected_asset = self.job.asset_set.create(name="protected_ImportantAsset")

        TransactionBroadcastManager._clear()
        RedisSession.init_database(JobContextChannel)
        MongoSignalWriter(JobContextChannel).init_database()

    async def safe_disconnect(self, ws):
        try:
            await ws.disconnect()
        except asyncio.CancelledError:
            pass

    async def connect_and_auth(self):
        ws = self.get_ws(self.job.id)
        connected, _ = await ws.connect()
        assert connected
        await ws.send_to(text_data=self.auth)
        return ws

    async def receive_state(self, ws):
        index = {}
        while True:
            try:
                response = await ws.receive_output(timeout=T)
                data = json.loads(response['text'])
                if isinstance(data, dict):
                    continue
                for instance in data:
                    key = make_key(instance)
                    if key is None:
                        return index
                    index[key] = instance
            except asyncio.TimeoutError:
                return index

    async def send_write(self, ws, write_id, operation, **kwargs):
        msg = {
            'type': 'write',
            'writeId': write_id,
            'operation': operation,
            **kwargs,
        }
        await ws.send_to(text_data=json.dumps(msg))

    async def receive_write_response(self, ws, write_id):
        while True:
            try:
                response = await ws.receive_output(timeout=T)
                data = json.loads(response['text'])
                if isinstance(data, dict) and data.get('type') == 'writeResponse':
                    if data.get('writeId') == write_id:
                        return data
            except asyncio.TimeoutError:
                return None

    async def test_save_with_forbidden_field_stripped(self):
        """Test that forbidden_field is stripped by field filtering before
        reaching can_save, so the operation succeeds with valid fields only."""
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        write_id = 4001
        await self.send_write(
            ws,
            write_id,
            'save',
            instanceType='react_test.serializers.TaskSerializer',
            instanceId=self.task.id,
            data={'name': 'Updated Name', 'forbidden_field': 'should be stripped'},
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response)
        # forbidden_field is not in the serializer, so it gets stripped.
        # The remaining data {'name': 'Updated Name'} passes can_save.
        self.assertTrue(response.get('success'))

        task = await database_sync_to_async(
            lambda: Task.objects.get(pk=self.task.id)
        )()
        self.assertEqual(task.name, 'Updated Name')

        await self.safe_disconnect(ws)

    async def test_create_with_forbidden_field_stripped(self):
        """Test that forbidden_field is stripped by field filtering before
        reaching can_create, so the operation succeeds with valid fields only."""
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        write_id = 4002
        await self.send_write(
            ws,
            write_id,
            'create',
            instanceType='react_test.serializers.TaskSerializer',
            parentType='react_test.serializers.JobNestedSerializer',
            parentId=self.job.id,
            relationName='tasks',
            data={'name': 'New Task', 'developer': self.participant.id, 'forbidden_field': 'should be stripped'},
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response)
        # forbidden_field is not in the serializer, so it gets stripped.
        # The remaining data passes can_create.
        self.assertTrue(response.get('success'))

        count = await database_sync_to_async(
            lambda: self.job.tasks.filter(name='New Task').count()
        )()
        self.assertEqual(count, 1)

        await self.safe_disconnect(ws)

    async def test_delete_protected_asset_rejected(self):
        """Test that delete of protected_ asset is rejected."""
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        write_id = 4003
        await self.send_write(
            ws,
            write_id,
            'delete',
            instanceType='react_test.serializers.AssetSerializer',
            instanceId=self.protected_asset.id,
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response)
        self.assertFalse(response.get('success'))
        self.assertEqual(response.get('error', {}).get('code'), 403)

        exists = await database_sync_to_async(
            lambda: Asset.objects.filter(pk=self.protected_asset.id).exists()
        )()
        self.assertTrue(exists)

        await self.safe_disconnect(ws)


class WritableDeclarationTestCase(TestCase):
    """Test that Meta.writable is correctly parsed into _writable map."""

    def test_writable_map_is_set(self):
        self.assertIsInstance(JobContextChannel._writable, dict)
        self.assertGreater(len(JobContextChannel._writable), 0)

    def test_writable_contains_task_serializer(self):
        from rxdjango.operations import SAVE, CREATE, DELETE
        key = 'react_test.serializers.TaskSerializer'
        self.assertIn(key, JobContextChannel._writable)
        self.assertEqual(
            sorted(JobContextChannel._writable[key], key=lambda op: op.name),
            sorted([SAVE, CREATE, DELETE], key=lambda op: op.name),
        )

    def test_writable_contains_asset_serializer(self):
        from rxdjango.operations import SAVE, CREATE, DELETE
        key = 'react_test.serializers.AssetSerializer'
        self.assertIn(key, JobContextChannel._writable)
        self.assertEqual(
            sorted(JobContextChannel._writable[key], key=lambda op: op.name),
            sorted([SAVE, CREATE, DELETE], key=lambda op: op.name),
        )

    def test_writable_does_not_contain_undeclared_types(self):
        """Types not listed in writable should not appear in _writable."""
        key = 'react_test.serializers.DeadlineSerializer'
        self.assertNotIn(key, JobContextChannel._writable)

    def test_channel_without_writable_has_empty_map(self):
        """A channel class that doesn't define Meta.writable gets empty map."""
        from rxdjango.channels import ContextChannel
        from react_test.serializers import JobNestedSerializer

        class NoWritableChannel(ContextChannel):
            class Meta:
                state = JobNestedSerializer()

            @staticmethod
            def has_permission(user, **kwargs):
                return True

        self.assertEqual(NoWritableChannel._writable, {})


class CrossContextWriteSecurityTests(TransactionTestCase):
    """Verify that write operations are rejected when the target instance
    does not belong to the channel's current anchor context.

    A client connected to JobContextChannel(job_id=1) must NOT be able to
    save/create/delete instances that belong to job_id=2.
    """

    def get_ws(self, job_id):
        application = URLRouter(websocket_urlpatterns)
        return WebsocketCommunicator(application, f'/ws/job/{job_id}/')

    def setUp(self):
        self.auth_user = AuthUser.objects.create_user(
            login='crossuser',
            password='crosspass',
        )
        self.token = Token.objects.create(user=self.auth_user)
        self.auth = json.dumps({'token': self.token.key})

        user1 = User.objects.create(name="Developer")
        project = Project.objects.create(name="Cross Context Project")
        self.participant = Participant.objects.create(
            project=project,
            user=user1,
            name="Cross Participant",
            role="Developer",
        )

        # Job 1: the anchor we connect to
        self.job1 = Job.objects.create(project=project, name="Job One")
        self.task1 = self.job1.tasks.create(name="Task in Job 1", developer=self.participant)
        self.asset1 = self.job1.asset_set.create(name="Asset in Job 1")

        # Job 2: a different anchor — instances here must NOT be writable
        # from a channel connected to job1
        self.job2 = Job.objects.create(project=project, name="Job Two")
        self.task2 = self.job2.tasks.create(name="Task in Job 2", developer=self.participant)
        self.asset2 = self.job2.asset_set.create(name="Asset in Job 2")

        TransactionBroadcastManager._clear()
        RedisSession.init_database(JobContextChannel)
        MongoSignalWriter(JobContextChannel).init_database()

    async def safe_disconnect(self, ws):
        try:
            await ws.disconnect()
        except asyncio.CancelledError:
            pass

    async def connect_and_auth(self, job_id):
        ws = self.get_ws(job_id)
        connected, _ = await ws.connect()
        assert connected
        await ws.send_to(text_data=self.auth)
        return ws

    async def receive_state(self, ws):
        index = {}
        while True:
            try:
                response = await ws.receive_output(timeout=T)
                data = json.loads(response['text'])
                if isinstance(data, dict):
                    continue
                for instance in data:
                    key = make_key(instance)
                    if key is None:
                        return index
                    index[key] = instance
            except asyncio.TimeoutError:
                return index

    async def send_write(self, ws, write_id, operation, **kwargs):
        msg = {
            'type': 'write',
            'writeId': write_id,
            'operation': operation,
            **kwargs,
        }
        await ws.send_to(text_data=json.dumps(msg))

    async def receive_write_response(self, ws, write_id):
        while True:
            try:
                response = await ws.receive_output(timeout=T)
                data = json.loads(response['text'])
                if isinstance(data, dict) and data.get('type') == 'writeResponse':
                    if data.get('writeId') == write_id:
                        return data
            except asyncio.TimeoutError:
                return None

    async def test_save_instance_outside_context_is_rejected(self):
        """Saving a Task that belongs to job2 via a channel connected to job1
        must fail — the instance is not in this channel's context."""
        ws = await self.connect_and_auth(self.job1.id)
        await self.receive_state(ws)

        write_id = 9001
        await self.send_write(
            ws,
            write_id,
            'save',
            instanceType='react_test.serializers.TaskSerializer',
            instanceId=self.task2.id,
            data={'name': 'Hacked Task Name'},
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response, "Expected a writeResponse but got timeout")
        self.assertFalse(
            response.get('success'),
            "Save should be rejected for an instance outside the channel context",
        )

        # Verify the database was NOT modified
        task = await database_sync_to_async(
            lambda: Task.objects.get(pk=self.task2.id)
        )()
        self.assertEqual(task.name, 'Task in Job 2')

        await self.safe_disconnect(ws)

    async def test_delete_instance_outside_context_is_rejected(self):
        """Deleting an Asset that belongs to job2 via a channel connected to
        job1 must fail."""
        ws = await self.connect_and_auth(self.job1.id)
        await self.receive_state(ws)

        write_id = 9002
        await self.send_write(
            ws,
            write_id,
            'delete',
            instanceType='react_test.serializers.AssetSerializer',
            instanceId=self.asset2.id,
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response, "Expected a writeResponse but got timeout")
        self.assertFalse(
            response.get('success'),
            "Delete should be rejected for an instance outside the channel context",
        )

        # Verify the instance still exists
        exists = await database_sync_to_async(
            lambda: Asset.objects.filter(pk=self.asset2.id).exists()
        )()
        self.assertTrue(exists, "Asset in job2 should not have been deleted")

        await self.safe_disconnect(ws)

    async def test_create_with_parent_outside_context_is_rejected(self):
        """Creating a Task under job2 via a channel connected to job1
        must fail — the parent is not in this channel's context."""
        ws = await self.connect_and_auth(self.job1.id)
        await self.receive_state(ws)

        write_id = 9003
        await self.send_write(
            ws,
            write_id,
            'create',
            instanceType='react_test.serializers.TaskSerializer',
            parentType='react_test.serializers.JobNestedSerializer',
            parentId=self.job2.id,
            relationName='tasks',
            data={'name': 'Injected Task', 'developer': self.participant.id},
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response, "Expected a writeResponse but got timeout")
        self.assertFalse(
            response.get('success'),
            "Create should be rejected when parent is outside the channel context",
        )

        # Verify no task was created under job2
        count = await database_sync_to_async(
            lambda: self.job2.tasks.count()
        )()
        self.assertEqual(count, 1, "job2 should still have exactly 1 task")

        await self.safe_disconnect(ws)

    async def test_save_own_context_still_works(self):
        """Sanity check: saving a Task that belongs to job1 via a channel
        connected to job1 should still succeed."""
        ws = await self.connect_and_auth(self.job1.id)
        await self.receive_state(ws)

        write_id = 9004
        await self.send_write(
            ws,
            write_id,
            'save',
            instanceType='react_test.serializers.TaskSerializer',
            instanceId=self.task1.id,
            data={'name': 'Legitimately Updated'},
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response, "Expected a writeResponse but got timeout")
        self.assertTrue(
            response.get('success'),
            "Save should succeed for an instance within the channel context",
        )

        task = await database_sync_to_async(
            lambda: Task.objects.get(pk=self.task1.id)
        )()
        self.assertEqual(task.name, 'Legitimately Updated')

        await self.safe_disconnect(ws)


class WritableDeclarationEnforcementTests(TransactionTestCase):
    """Verify that write operations are rejected when the instance type
    is not declared in Meta.writable, even if the instance is in context
    and can_* would return True.

    DeadlineSerializer is part of the state but NOT in Meta.writable.
    """

    def get_ws(self, job_id):
        application = URLRouter(websocket_urlpatterns)
        return WebsocketCommunicator(application, f'/ws/job/{job_id}/')

    def setUp(self):
        self.auth_user = AuthUser.objects.create_user(
            login='writabledecluser',
            password='writabledeclpass',
        )
        self.token = Token.objects.create(user=self.auth_user)
        self.auth = json.dumps({'token': self.token.key})

        user1 = User.objects.create(name="Developer")
        project = Project.objects.create(name="Writable Decl Project")
        self.participant = Participant.objects.create(
            project=project,
            user=user1,
            name="Decl Participant",
            role="Developer",
        )

        self.job = Job.objects.create(project=project, name="Writable Decl Job")
        self.task = self.job.tasks.create(name="A Task", developer=self.participant)
        self.deadline = Deadline.objects.create(job=self.job, name="Q1 Deadline")

        TransactionBroadcastManager._clear()
        RedisSession.init_database(JobContextChannel)
        MongoSignalWriter(JobContextChannel).init_database()

    async def safe_disconnect(self, ws):
        try:
            await ws.disconnect()
        except asyncio.CancelledError:
            pass

    async def connect_and_auth(self):
        ws = self.get_ws(self.job.id)
        connected, _ = await ws.connect()
        assert connected
        await ws.send_to(text_data=self.auth)
        return ws

    async def receive_state(self, ws):
        index = {}
        while True:
            try:
                response = await ws.receive_output(timeout=T)
                data = json.loads(response['text'])
                if isinstance(data, dict):
                    continue
                for instance in data:
                    key = make_key(instance)
                    if key is None:
                        return index
                    index[key] = instance
            except asyncio.TimeoutError:
                return index

    async def send_write(self, ws, write_id, operation, **kwargs):
        msg = {
            'type': 'write',
            'writeId': write_id,
            'operation': operation,
            **kwargs,
        }
        await ws.send_to(text_data=json.dumps(msg))

    async def receive_write_response(self, ws, write_id):
        while True:
            try:
                response = await ws.receive_output(timeout=T)
                data = json.loads(response['text'])
                if isinstance(data, dict) and data.get('type') == 'writeResponse':
                    if data.get('writeId') == write_id:
                        return data
            except asyncio.TimeoutError:
                return None

    async def test_save_undeclared_type_is_rejected(self):
        """Saving a Deadline (not in Meta.writable) should be rejected
        even though it's in context and can_save returns True."""
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        write_id = 8001
        await self.send_write(
            ws,
            write_id,
            'save',
            instanceType='react_test.serializers.DeadlineSerializer',
            instanceId=self.deadline.id,
            data={'name': 'Hacked Deadline'},
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response, "Expected a writeResponse but got timeout")
        self.assertFalse(
            response.get('success'),
            "Save should be rejected for a type not declared in Meta.writable",
        )

        deadline = await database_sync_to_async(
            lambda: Deadline.objects.get(pk=self.deadline.id)
        )()
        self.assertEqual(deadline.name, 'Q1 Deadline')

        await self.safe_disconnect(ws)

    async def test_delete_undeclared_type_is_rejected(self):
        """Deleting a Deadline (not in Meta.writable) should be rejected."""
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        write_id = 8002
        await self.send_write(
            ws,
            write_id,
            'delete',
            instanceType='react_test.serializers.DeadlineSerializer',
            instanceId=self.deadline.id,
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response, "Expected a writeResponse but got timeout")
        self.assertFalse(
            response.get('success'),
            "Delete should be rejected for a type not declared in Meta.writable",
        )

        exists = await database_sync_to_async(
            lambda: Deadline.objects.filter(pk=self.deadline.id).exists()
        )()
        self.assertTrue(exists, "Deadline should not have been deleted")

        await self.safe_disconnect(ws)

    async def test_create_undeclared_type_is_rejected(self):
        """Creating a Deadline (not in Meta.writable) under the job should
        be rejected even though the parent (job) is in context."""
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        write_id = 8003
        await self.send_write(
            ws,
            write_id,
            'create',
            instanceType='react_test.serializers.DeadlineSerializer',
            parentType='react_test.serializers.JobNestedSerializer',
            parentId=self.job.id,
            relationName='deadline_set',
            data={'name': 'Injected Deadline'},
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response, "Expected a writeResponse but got timeout")
        self.assertFalse(
            response.get('success'),
            "Create should be rejected for a type not declared in Meta.writable",
        )

        count = await database_sync_to_async(
            lambda: Deadline.objects.filter(job=self.job).count()
        )()
        self.assertEqual(count, 1, "No new deadline should have been created")

        await self.safe_disconnect(ws)

    async def test_declared_type_still_works(self):
        """Sanity check: saving a Task (declared in Meta.writable) should
        still succeed."""
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        write_id = 8004
        await self.send_write(
            ws,
            write_id,
            'save',
            instanceType='react_test.serializers.TaskSerializer',
            instanceId=self.task.id,
            data={'name': 'Allowed Update'},
        )

        response = await self.receive_write_response(ws, write_id)
        self.assertIsNotNone(response, "Expected a writeResponse but got timeout")
        self.assertTrue(
            response.get('success'),
            "Save should succeed for a type declared in Meta.writable",
        )

        task = await database_sync_to_async(
            lambda: Task.objects.get(pk=self.task.id)
        )()
        self.assertEqual(task.name, 'Allowed Update')

        await self.safe_disconnect(ws)
