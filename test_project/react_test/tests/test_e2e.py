import os
import subprocess
import tempfile
import shutil
import time
import json
import asyncio
import threading
import socket
from pathlib import Path

from django.test import TestCase, TransactionTestCase
from django.contrib.auth import get_user_model
from django.urls import path
from channels.testing import WebsocketCommunicator
from channels.routing import URLRouter
from channels.db import database_sync_to_async
from rest_framework.authtoken.models import Token
from rxdjango.sdk import make_sdk

from react_test.models import User, Project, Participant, Job
from react_test.channels import JobContextChannel
from rxdjango.mongo import MongoSignalWriter
from rxdjango.redis import RedisSession


class E2ETestCase(TestCase):
    def setUp(self):
        self.frontend_dir = Path(__file__).parent.parent.parent / 'frontend'
        self.rxdjango_react_dir = Path(__file__).parent.parent.parent.parent / 'rxdjango-react'

    def _run_makefrontend(self):
        make_sdk(apply_changes=True, force=True)

    def test_generated_typescript_compiles(self):
        self._run_makefrontend()

        channels_ts = self.frontend_dir / 'react_test' / 'react_test.channels.ts'
        interfaces_ts = self.frontend_dir / 'react_test' / 'react_test.interfaces.d.ts'

        self.assertTrue(channels_ts.exists(), f"channels.ts not generated at {channels_ts}")
        self.assertTrue(interfaces_ts.exists(), f"interfaces.d.ts not generated at {interfaces_ts}")

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            node_modules_src = self.rxdjango_react_dir / 'node_modules'
            node_modules_dst = tmpdir / 'node_modules'
            if node_modules_src.exists():
                shutil.copytree(node_modules_src, node_modules_dst)

            dist_src = self.rxdjango_react_dir / 'dist'
            dist_dst = tmpdir / 'node_modules' / '@rxdjango' / 'react'
            if dist_src.exists():
                os.makedirs(dist_dst, exist_ok=True)
                shutil.copytree(dist_src, dist_dst, dirs_exist_ok=True)

            shutil.copytree(self.frontend_dir / 'react_test', tmpdir / 'react_test')

            tsconfig = {
                "compilerOptions": {
                    "target": "ES2020",
                    "module": "commonjs",
                    "strict": True,
                    "esModuleInterop": True,
                    "skipLibCheck": True,
                    "forceConsistentCasingInFileNames": True,
                    "moduleResolution": "node",
                    "noEmit": True,
                },
                "include": ["react_test/**/*.ts"]
            }

            with open(tmpdir / 'tsconfig.json', 'w') as f:
                json.dump(tsconfig, f)

            tsc_path = self.rxdjango_react_dir / 'node_modules' / '.bin' / 'tsc'
            if not tsc_path.exists():
                tsc_path = Path('tsc')

            result = subprocess.run(
                [str(tsc_path), '--project', str(tmpdir / 'tsconfig.json')],
                capture_output=True,
                text=True,
                cwd=str(tmpdir)
            )

            if result.returncode != 0:
                channels_content = channels_ts.read_text()
                self.fail(
                    f"Generated TypeScript has syntax errors:\n"
                    f"tsc stdout:\n{result.stdout}\n"
                    f"tsc stderr:\n{result.stderr}\n"
                    f"Generated channels.ts content:\n{channels_content}"
                )

    def test_channels_ts_socket_url_is_quoted(self):
        self._run_makefrontend()

        channels_ts = self.frontend_dir / 'react_test' / 'react_test.channels.ts'
        content = channels_ts.read_text()

        self.assertIn('const SOCKET_URL = "', content,
                      "SOCKET_URL should be a quoted string literal")
        self.assertNotRegex(content, r'const SOCKET_URL = http',
                           "SOCKET_URL should not have unquoted URL")

    def test_channels_ts_no_double_braces(self):
        self._run_makefrontend()

        channels_ts = self.frontend_dir / 'react_test' / 'react_test.channels.ts'
        content = channels_ts.read_text()

        lines = content.split('\n')
        for i, line in enumerate(lines):
            if '}}' in line and 'interface' not in line.lower() and '{' not in line.split('}}')[0][-5:]:
                self.fail(
                    f"Line {i+1} has '}}' which is likely a syntax error:\n{line}"
                )

    def test_generated_channel_has_correct_methods(self):
        """Test that generated channel has action methods matching backend."""
        self._run_makefrontend()

        channels_ts = self.frontend_dir / 'react_test' / 'react_test.channels.ts'
        content = channels_ts.read_text()

        self.assertIn('renameJob', content, "Should have renameJob method")
        self.assertIn('createTask', content, "Should have createTask method")
        self.assertIn('renameAsset', content, "Should have renameAsset method")
        self.assertIn('deleteAsset', content, "Should have deleteAsset method")

        self.assertIn("callAction('rename_job'", content)
        self.assertIn("callAction('create_task'", content)
        self.assertIn("callAction('rename_asset'", content)
        self.assertIn("callAction('delete_asset'", content)


T = 1


def make_key(instance):
    _type = instance.get('_instance_type', '')
    if not _type:
        return None
    return f"{_type}:{instance['id']}"


websocket_urlpatterns = [
    path('ws/job/<int:job_id>/', JobContextChannel.as_asgi()),
]


class E2EIntegrationTestCase(TransactionTestCase):
    """Integration tests for backend-frontend communication."""

    def get_ws(self, job_id):
        application = URLRouter(websocket_urlpatterns)
        return WebsocketCommunicator(application, f'/ws/job/{job_id}/')

    def setUp(self):
        AuthUser = get_user_model()
        self.auth_user = AuthUser.objects.create_user(
            login='e2euser',
            password='e2epass',
        )
        self.token = Token.objects.create(user=self.auth_user)
        self.auth = json.dumps({'token': self.token.key})

        user1 = User.objects.create(name="Developer")
        project = Project.objects.create(name="E2E Project")
        self.participant = Participant.objects.create(
            project=project,
            user=user1,
            name="E2E Participant",
            role="Developer",
        )
        self.job = Job.objects.create(project=project, name="E2E Job")
        self.job.tasks.create(name="Initial Task", developer=self.participant)
        self.job.asset_set.create(name="Initial Asset")

        from rxdjango.transaction_manager import TransactionBroadcastManager
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

    async def call_action(self, ws, action, *params, call_id=1):
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

    async def test_state_properly_loaded(self):
        """Test that initial state contains all expected instances."""
        ws = await self.connect_and_auth()

        response = await ws.receive_output(timeout=T)
        data = json.loads(response['text'])
        self.assertEqual(data['statusCode'], 200)

        state = await self.receive_state(ws)

        job_key = f'react_test.serializers.JobNestedSerializer:{self.job.id}'
        self.assertIn(job_key, state)
        self.assertEqual(state[job_key]['name'], 'E2E Job')
        self.assertEqual(len(state[job_key]['tasks']), 1)
        self.assertEqual(len(state[job_key]['asset_set']), 1)

        project_key = f'react_test.serializers.ProjectSerializer:{self.job.project.id}'
        self.assertIn(project_key, state)
        self.assertEqual(state[project_key]['name'], 'E2E Project')

        task = await database_sync_to_async(lambda: self.job.tasks.first())()
        task_key = f'react_test.serializers.TaskSerializer:{task.id}'
        self.assertIn(task_key, state)
        self.assertEqual(state[task_key]['name'], 'Initial Task')

        await self.safe_disconnect(ws)

    async def test_action_edits_database(self):
        """Test that calling an action edits the database correctly."""
        ws = await self.connect_and_auth()
        await self.receive_state(ws)

        result, updates = await self.call_action(
            ws,
            'rename_job',
            'Renamed Via Action',
            call_id=1,
        )

        self.assertEqual(result['type'], 'actionResponse')
        self.assertEqual(result['callId'], 1)
        self.assertEqual(result['result']['name'], 'Renamed Via Action')
        self.assertEqual(result['result']['jobId'], self.job.id)

        job = await database_sync_to_async(lambda: Job.objects.get(pk=self.job.id))()
        self.assertEqual(job.name, 'Renamed Via Action')

        job_key = f'react_test.serializers.JobNestedSerializer:{self.job.id}'
        self.assertIn(job_key, updates)
        self.assertEqual(updates[job_key]['name'], 'Renamed Via Action')

        await self.safe_disconnect(ws)

    async def test_state_built_after_database_edit(self):
        """Test that state is properly rebuilt after direct database edit."""
        from channels.db import database_sync_to_async

        ws = await self.connect_and_auth()
        state = await self.receive_state(ws)

        job_key = f'react_test.serializers.JobNestedSerializer:{self.job.id}'
        self.assertEqual(state[job_key]['name'], 'E2E Job')

        await database_sync_to_async(self._update_job)()

        updates = {}
        while True:
            try:
                response = await ws.receive_output(timeout=T)
                data = json.loads(response['text'])
                if isinstance(data, list):
                    for instance in data:
                        key = make_key(instance)
                        if key:
                            updates[key] = instance
                    if job_key in updates:
                        break
            except asyncio.TimeoutError:
                break

        self.assertIn(job_key, updates)
        self.assertEqual(updates[job_key]['name'], 'Updated Via Database')

        await self.safe_disconnect(ws)

    def _update_job(self):
        self.job.name = 'Updated Via Database'
        self.job.save()
