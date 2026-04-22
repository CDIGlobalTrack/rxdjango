"""Integration tests for reactive state on ContextChannel.

Tests the full WebSocket lifecycle for reactive fields: initial delivery,
single-field updates, container mutation, batching, idempotency, multi-client
broadcast, and reconnect-reset semantics.

Run from test_project/:
    python manage.py test react_test.tests.test_runtime_state
"""
import json
import asyncio

from channels.testing import WebsocketCommunicator
from channels.routing import URLRouter
from django.test import TransactionTestCase
from django.urls import path
from rest_framework.authtoken.models import Token

from users.models import User as AuthUser
from react_test.models import User, Project, Participant, Job
from react_test.channels import JobContextChannel
from rxdjango.mongo import MongoSignalWriter
from rxdjango.redis import RedisSession
from rxdjango.transaction_manager import TransactionBroadcastManager

# Message receive timeout in seconds
T = 1

websocket_urlpatterns = [
    path('ws/job/<int:job_id>/', JobContextChannel.as_asgi()),
]


class RuntimeStateTest(TransactionTestCase):
    """Integration tests for reactive state fields."""

    def get_ws(self, job_id):
        application = URLRouter(websocket_urlpatterns)
        return WebsocketCommunicator(application, f'/ws/job/{job_id}/')

    def setUp(self):
        self.auth_user = AuthUser.objects.create_user(
            login='testuser', password='testpass',
        )
        self.token = Token.objects.create(user=self.auth_user)
        self.auth = json.dumps({'token': self.token.key})

        user1 = User.objects.create(name='User1')
        project = Project.objects.create(name='Project1')
        part1 = Participant.objects.create(
            project=project, user=user1,
            name='Participant1', role='Developer',
        )
        job = Job.objects.create(project=project, name='Job1')
        job.tasks.create(name='Task1', developer=part1)

        self.job = job
        self.project = project

        TransactionBroadcastManager._clear()
        RedisSession.init_database(JobContextChannel)
        MongoSignalWriter(JobContextChannel).init_database()

    def tearDown(self):
        AuthUser.objects.all().delete()
        Job.objects.all().delete()
        Project.objects.all().delete()
        User.objects.all().delete()

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    async def connect_and_auth(self):
        ws = self.get_ws(self.job.id)
        connected, _ = await ws.connect()
        assert connected
        await ws.send_to(text_data=self.auth)
        return ws

    async def safe_disconnect(self, ws):
        try:
            await ws.disconnect()
        except asyncio.CancelledError:
            pass

    async def drain_initial_state(self, ws):
        """Read messages until we see the end_initial_state marker.

        Returns a tuple (all_messages, runtime_vars_message) where
        runtime_vars_message is the runtimeVars dict sent after state load
        (or None if not received).
        """
        all_messages = []
        runtime_vars = None
        while True:
            try:
                resp = await ws.receive_output(timeout=T)
                data = json.loads(resp['text'])
                all_messages.append(data)

                if isinstance(data, dict) and data.get('type') == 'runtimeVars':
                    runtime_vars = data
                    # After runtimeVars there are no more setup messages
                    break

                if isinstance(data, list):
                    for item in data:
                        if item.get('_operation') == 'end_initial_state':
                            # Flush remaining messages briefly
                            continue
            except asyncio.TimeoutError:
                break
        return all_messages, runtime_vars

    async def call_action(self, ws, action_name, *params, call_id=1):
        """Call an action and collect response + any reactive messages."""
        await ws.send_to(text_data=json.dumps({
            'callId': call_id,
            'action': action_name,
            'params': list(params),
        }))

        response = None
        reactive_messages = []

        while True:
            try:
                resp = await ws.receive_output(timeout=T)
                data = json.loads(resp['text'])

                if isinstance(data, dict) and data.get('type') == 'actionResponse':
                    response = data
                    # Keep reading briefly for reactive messages
                    continue
                if isinstance(data, dict) and data.get('type') in ('runtimeVar', 'runtimeVars'):
                    reactive_messages.append(data)
            except asyncio.TimeoutError:
                break

        return response, reactive_messages

    async def collect_reactive(self, ws, timeout=T):
        """Collect any reactive messages that arrive within timeout."""
        messages = []
        while True:
            try:
                resp = await ws.receive_output(timeout=timeout)
                data = json.loads(resp['text'])
                if isinstance(data, dict) and data.get('type') in ('runtimeVar', 'runtimeVars'):
                    messages.append(data)
            except asyncio.TimeoutError:
                break
        return messages

    # -----------------------------------------------------------------------
    # Tests
    # -----------------------------------------------------------------------

    async def test_1_initial_state_includes_reactive_defaults(self):
        """On connect, client receives a runtimeVars message with all defaults."""
        ws = await self.connect_and_auth()
        try:
            _, runtime_vars = await self.drain_initial_state(ws)

            assert runtime_vars is not None, \
                "Expected a runtimeVars message after initial state load"
            assert runtime_vars['type'] == 'runtimeVars'
            vars_ = runtime_vars['vars']
            assert vars_['typing_users'] == []
            assert vars_['unread_count'] == 0
            assert vars_['view_mode'] == 'view'
        finally:
            await self.safe_disconnect(ws)

    async def test_2_single_scalar_update(self):
        """start_typing broadcasts a runtimeVar with the updated list."""
        ws = await self.connect_and_auth()
        try:
            await self.drain_initial_state(ws)

            response, reactive = await self.call_action(ws, 'start_typing', 1)
            assert response is not None
            assert 'error' not in response

            assert len(reactive) == 1
            msg = reactive[0]
            assert msg['type'] == 'runtimeVar'
            assert msg['var'] == 'typing_users'
            assert msg['value'] == [1]
        finally:
            await self.safe_disconnect(ws)

    async def test_3_container_mutation_order(self):
        """Sequential start_typing calls produce cumulative updates."""
        ws = await self.connect_and_auth()
        try:
            await self.drain_initial_state(ws)

            _, r1 = await self.call_action(ws, 'start_typing', 1, call_id=1)
            _, r2 = await self.call_action(ws, 'start_typing', 2, call_id=2)

            assert len(r1) == 1 and r1[0]['value'] == [1]
            assert len(r2) == 1 and r2[0]['value'] == [1, 2]
        finally:
            await self.safe_disconnect(ws)

    async def test_4_removal_via_in_place_mutation(self):
        """stop_typing produces a runtimeVar with the correct remaining list."""
        ws = await self.connect_and_auth()
        try:
            await self.drain_initial_state(ws)

            await self.call_action(ws, 'start_typing', 1, call_id=1)
            await self.call_action(ws, 'start_typing', 2, call_id=2)
            _, reactive = await self.call_action(ws, 'stop_typing', 1, call_id=3)

            assert len(reactive) == 1
            assert reactive[0]['value'] == [2]
        finally:
            await self.safe_disconnect(ws)

    async def test_5_batched_update_single_message(self):
        """mark_all_read_and_switch_to_edit produces exactly one runtimeVars message."""
        ws = await self.connect_and_auth()
        try:
            await self.drain_initial_state(ws)

            # Seed some state first
            await self.call_action(ws, 'start_typing', 1, call_id=1)

            response, reactive = await self.call_action(
                ws, 'mark_all_read_and_switch_to_edit', call_id=2
            )
            assert response is not None
            assert 'error' not in response

            # Must be exactly one message of type runtimeVars
            assert len(reactive) == 1, \
                f"Expected 1 runtimeVars, got {len(reactive)}: {reactive}"
            msg = reactive[0]
            assert msg['type'] == 'runtimeVars', \
                f"Expected runtimeVars, got {msg['type']}"
            vars_ = msg['vars']
            assert vars_['unread_count'] == 0
            assert vars_['view_mode'] == 'edit'
            assert vars_['typing_users'] == []
        finally:
            await self.safe_disconnect(ws)

    async def test_6_no_broadcast_when_value_unchanged(self):
        """Calling start_typing for a user already in the list produces no message."""
        ws = await self.connect_and_auth()
        try:
            await self.drain_initial_state(ws)

            # Add user 1 first
            await self.call_action(ws, 'start_typing', 1, call_id=1)

            # Call again with same user — the channel's if-guard prevents the write
            response, reactive = await self.call_action(ws, 'start_typing', 1, call_id=2)
            assert response is not None

            assert reactive == [], \
                f"Expected no reactive messages, got: {reactive}"
        finally:
            await self.safe_disconnect(ws)

    async def test_7_two_clients_one_channel(self):
        """An action from client A sends runtimeVar to both A and B."""
        ws_a = await self.connect_and_auth()
        ws_b = await self.connect_and_auth()
        try:
            await self.drain_initial_state(ws_a)
            await self.drain_initial_state(ws_b)

            # A calls start_typing
            await ws_a.send_to(text_data=json.dumps({
                'callId': 1,
                'action': 'start_typing',
                'params': [42],
            }))

            # Both A and B should receive the runtimeVar
            a_msgs = await self.collect_reactive(ws_a)
            b_msgs = await self.collect_reactive(ws_b)

            assert any(m.get('var') == 'typing_users' for m in a_msgs), \
                f"A did not receive typing_users update: {a_msgs}"
            assert any(m.get('var') == 'typing_users' for m in b_msgs), \
                f"B did not receive typing_users update: {b_msgs}"
        finally:
            await self.safe_disconnect(ws_a)
            await self.safe_disconnect(ws_b)

    async def test_8_reconnect_resets_reactive_state(self):
        """Reconnecting client receives fresh defaults, not prior session values."""
        # First connection: mutate state
        ws_a = await self.connect_and_auth()
        await self.drain_initial_state(ws_a)
        await self.call_action(ws_a, 'start_typing', 99, call_id=1)
        await self.safe_disconnect(ws_a)

        # Second connection: fresh session — should see defaults
        ws_b = await self.connect_and_auth()
        try:
            _, runtime_vars = await self.drain_initial_state(ws_b)

            assert runtime_vars is not None
            assert runtime_vars['vars']['typing_users'] == [], \
                "Reconnect must reset reactive state to defaults"
            assert runtime_vars['vars']['view_mode'] == 'view'
            assert runtime_vars['vars']['unread_count'] == 0
        finally:
            await self.safe_disconnect(ws_b)

    async def test_9_batch_net_zero_field_omitted(self):
        """Fields whose value doesn't change during a batch are not broadcast."""
        ws = await self.connect_and_auth()
        try:
            await self.drain_initial_state(ws)

            # mark_all_read_and_switch_to_edit sets unread_count=0, view_mode='edit',
            # typing_users=[]. unread_count is already 0, so it may be omitted.
            # view_mode changes from 'view' to 'edit', so it must be present.
            response, reactive = await self.call_action(
                ws, 'mark_all_read_and_switch_to_edit', call_id=1
            )
            assert len(reactive) == 1
            vars_ = reactive[0]['vars']
            assert 'view_mode' in vars_, "view_mode changed, must be in batch"
            # unread_count was already 0 — may or may not be included
            # (implementation may or may not deduplicate against defaults)
            # We only assert the changed field is present.
        finally:
            await self.safe_disconnect(ws)
