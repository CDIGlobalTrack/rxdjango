"""
Tests for WebSocket connection, authentication, and real-time updates.

Adapted from gt-nx-react project/tests/test_websocket.py.
These tests verify the full WebSocket lifecycle: connect, authenticate,
receive initial state, and receive real-time updates on model changes.
"""
import json
import asyncio
from channels.testing import WebsocketCommunicator
from channels.routing import URLRouter
from channels.db import database_sync_to_async
from django.test import TransactionTestCase
from django.urls import path
from rest_framework.authtoken.models import Token
from users.models import User as AuthUser
from react_test.models import User, Project, Participant, Job, Task
from react_test.channels import JobContextChannel
from rxdjango.mongo import MongoSignalWriter
from rxdjango.redis import RedisSession
from rxdjango.transaction_manager import TransactionBroadcastManager

# Timeout for receiving WebSocket messages
T = 1


def make_key(instance):
    _type = instance.get('_instance_type', '')
    if not _type:
        return None
    return f"{_type}:{instance['id']}"


websocket_urlpatterns = [
    path('ws/job/<int:job_id>/', JobContextChannel.as_asgi()),
]


class WebsocketStateTest(TransactionTestCase):
    """Test WebSocket connection flow and initial state loading."""

    def get_ws(self, job_id):
        application = URLRouter(websocket_urlpatterns)
        return WebsocketCommunicator(application, f'/ws/job/{job_id}/')

    def setUp(self):
        # Create auth user for token-based authentication
        self.auth_user = AuthUser.objects.create_user(
            login='testuser', password='testpass',
        )
        self.token = Token.objects.create(user=self.auth_user)
        self.auth = json.dumps({'token': self.token.key})

        # Create domain data
        user1 = User.objects.create(name="User1")
        user2 = User.objects.create(name="User2")
        project = Project.objects.create(name="Project1")
        part1 = Participant.objects.create(
            project=project, user=user1,
            name="Participant1", role="Developer",
        )
        part2 = Participant.objects.create(
            project=project, user=user2,
            name="Participant2", role="Designer",
        )
        job = Job.objects.create(project=project, name="Job1")
        job.tasks.create(name="Task1", developer=part1)
        job.tasks.create(name="Task2", developer=part2)
        job.asset_set.create(name="Asset1")
        job.deadline_set.create(name="Deadline1")

        self.job = job
        self.project = project
        self.user1 = user1
        self.part1 = part1

        TransactionBroadcastManager._clear()

        # Initialize cache
        RedisSession.init_database(JobContextChannel)
        MongoSignalWriter(JobContextChannel).init_database()

    def tearDown(self):
        AuthUser.objects.all().delete()
        Job.objects.all().delete()
        Project.objects.all().delete()
        User.objects.all().delete()

    async def safe_disconnect(self, ws):
        """Disconnect handling CancelledError (consumer may crash on cleanup)."""
        try:
            await ws.disconnect()
        except asyncio.CancelledError:
            pass

    async def connect_and_auth(self):
        """Connect to WebSocket and authenticate."""
        ws = self.get_ws(self.job.id)
        connected, _ = await ws.connect()
        assert connected
        await ws.send_to(text_data=self.auth)
        return ws

    async def receive_state(self, ws):
        """Receive all initial state messages until end marker."""
        index = {}
        while True:
            try:
                response = await ws.receive_output(timeout=T)
                data = json.loads(response['text'])
                if isinstance(data, dict):
                    # Status or header message
                    continue
                for instance in data:
                    key = make_key(instance)
                    if key is None:
                        # End-of-data marker
                        return index
                    index[key] = instance
            except asyncio.TimeoutError:
                return index

    async def receive_updates(self, ws):
        """Receive update messages until timeout."""
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

    async def call_action(self, ws, action, *params, call_id=1):
        """Call a websocket action and collect its response and updates."""
        await ws.send_to(text_data=json.dumps({
            'callId': call_id,
            'action': action,
            'params': list(params),
        }))

        result = None
        updates = {}

        while True:
            try:
                timeout = T * 2 if result is not None else T
                response = await ws.receive_output(timeout=timeout)
                data = json.loads(response['text'])

                if isinstance(data, dict) and data.get('callId') == call_id:
                    result = data
                    continue

                if isinstance(data, list):
                    for instance in data:
                        key = make_key(instance)
                        if key:
                            updates[key] = instance
                elif isinstance(data, dict) and '_instance_type' in data:
                    key = make_key(data)
                    if key:
                        updates[key] = data
            except asyncio.TimeoutError:
                return result, updates

    async def test_websocket_connect_and_authenticate(self):
        ws = self.get_ws(self.job.id)
        connected, _ = await ws.connect()
        self.assertTrue(connected)
        await ws.send_to(text_data=self.auth)

        # Should receive status 200
        response = await ws.receive_output(timeout=T)
        data = json.loads(response['text'])
        self.assertEqual(data['statusCode'], 200)

        await self.safe_disconnect(ws)

    async def test_invalid_token_returns_401(self):
        ws = self.get_ws(self.job.id)
        connected, _ = await ws.connect()
        self.assertTrue(connected)
        await ws.send_to(text_data=json.dumps({'token': 'invalid'}))

        response = await ws.receive_output(timeout=T)
        data = json.loads(response['text'])
        self.assertEqual(data['statusCode'], 401)

    async def test_initial_state_contains_anchor(self):
        """The anchor (job) should be in initial state."""
        ws = await self.connect_and_auth()
        state = await self.receive_state(ws)

        job_key = f'react_test.serializers.JobNestedSerializer:{self.job.id}'
        self.assertIn(job_key, state)

        await self.safe_disconnect(ws)

    async def test_initial_state_contains_nested_models(self):
        """Nested models (project, participants, tasks) should be in initial state."""
        ws = await self.connect_and_auth()
        state = await self.receive_state(ws)

        proj_key = f'react_test.serializers.ProjectSerializer:{self.project.id}'
        self.assertIn(proj_key, state)

        user_key = f'react_test.serializers.UserSerializer:{self.user1.id}'
        self.assertIn(user_key, state)

        part_key = f'react_test.serializers.ParticipantSerializer:{self.part1.id}'
        self.assertIn(part_key, state)

        await self.safe_disconnect(ws)

    async def test_model_update_sent_as_realtime_update(self):
        """Saving a model should send an update to connected clients."""
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        # Update the project name
        await database_sync_to_async(self._update_project)()

        updates = await self.receive_updates(ws)

        proj_key = f'react_test.serializers.ProjectSerializer:{self.project.id}'
        self.assertIn(proj_key, updates)

        await self.safe_disconnect(ws)

    def _update_project(self):
        self.project.name = 'Updated Project'
        self.project.save()

    async def test_nested_model_update_sent_as_realtime_update(self):
        """Saving a deeply nested model should send an update."""
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        await database_sync_to_async(self._update_user)()

        updates = await self.receive_updates(ws)

        user_key = f'react_test.serializers.UserSerializer:{self.user1.id}'
        self.assertIn(user_key, updates)

        await self.safe_disconnect(ws)

    def _update_user(self):
        self.user1.name = 'Updated User'
        self.user1.save()

    async def test_new_task_sent_as_realtime_update(self):
        """Creating a new related instance should send an update."""
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        new_task = await database_sync_to_async(self._create_task)()

        updates = await self.receive_updates(ws)

        task_key = f'react_test.serializers.TaskSerializer:{new_task.id}'
        self.assertIn(task_key, updates)

        await self.safe_disconnect(ws)

    def _create_task(self):
        return self.job.tasks.create(name="NewTask", developer=self.part1)

    async def test_action_can_update_anchor_and_return_result(self):
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        result, updates = await self.call_action(
            ws,
            'rename_job',
            'Renamed From Action',
            call_id=11,
        )

        await database_sync_to_async(self.job.refresh_from_db)()

        self.assertEqual(result, {
            'callId': 11,
            'result': {
                'jobId': self.job.id,
                'name': 'Renamed From Action',
            },
        })
        self.assertEqual(self.job.name, 'Renamed From Action')

        job_key = f'react_test.serializers.JobNestedSerializer:{self.job.id}'
        self.assertIn(job_key, updates)
        self.assertEqual(updates[job_key]['name'], 'Renamed From Action')

        await self.safe_disconnect(ws)

    async def test_action_can_create_nested_model(self):
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        result, updates = await self.call_action(
            ws,
            'create_task',
            'Action Task',
            self.part1.id,
            call_id=12,
        )

        created_task = await database_sync_to_async(
            lambda: Task.objects.get(name='Action Task', job=self.job)
        )()

        self.assertEqual(result, {
            'callId': 12,
            'result': {
                'taskId': created_task.id,
                'name': 'Action Task',
                'developerId': self.part1.id,
            },
        })

        task_key = f'react_test.serializers.TaskSerializer:{created_task.id}'
        self.assertIn(task_key, updates)
        self.assertEqual(updates[task_key]['name'], 'Action Task')

        await self.safe_disconnect(ws)

    async def test_action_can_update_existing_nested_model(self):
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        asset = await database_sync_to_async(lambda: self.job.asset_set.first())()

        result, updates = await self.call_action(
            ws,
            'rename_asset',
            asset.id,
            'Renamed Asset',
            call_id=13,
        )

        updated_asset = await database_sync_to_async(
            lambda: self.job.asset_set.get(pk=asset.id)
        )()

        self.assertEqual(result, {
            'callId': 13,
            'result': {
                'assetId': asset.id,
                'name': 'Renamed Asset',
            },
        })
        self.assertEqual(updated_asset.name, 'Renamed Asset')

        asset_key = f'react_test.serializers.AssetSerializer:{asset.id}'
        self.assertIn(asset_key, updates)
        self.assertEqual(updates[asset_key]['name'], 'Renamed Asset')

        await self.safe_disconnect(ws)

    async def test_action_can_delete_nested_model(self):
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        asset = await database_sync_to_async(lambda: self.job.asset_set.first())()

        result, updates = await self.call_action(
            ws,
            'delete_asset',
            asset.id,
            call_id=14,
        )

        exists = await database_sync_to_async(
            lambda: self.job.asset_set.filter(pk=asset.id).exists()
        )()

        self.assertEqual(result, {
            'callId': 14,
            'result': {
                'assetId': asset.id,
                'deleted': True,
            },
        })
        self.assertFalse(exists)

        await self.safe_disconnect(ws)

        asset_key = f'react_test.serializers.AssetSerializer:{asset.id}'
        job_key = f'react_test.serializers.JobNestedSerializer:{self.job.id}'
        for attempt in range(3):
            ws = await self.connect_and_auth()
            state = await self.receive_state(ws)
            asset_present = asset_key in state
            asset_linked = asset.id in state.get(job_key, {}).get('asset_set', [])
            await self.safe_disconnect(ws)

            if not asset_present and not asset_linked:
                break

            await asyncio.sleep(0.1)
        else:
            self.fail(f'{asset_key} remained in websocket state after delete action')
