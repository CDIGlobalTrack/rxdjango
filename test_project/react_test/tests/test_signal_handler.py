"""
Tests for signal handler broadcasts.

Adapted from gt-nx-react inspection/tests/test_websocket.py pattern.
These tests verify that model saves trigger correct broadcasts through
the SignalHandler, using the TransactionBroadcastManager for deduplication.
"""
from unittest.mock import patch
from django.test import TestCase
from rxdjango.transaction_manager import TransactionBroadcastManager
from react_test.models import User, Project, Participant, Job, Task, Asset
from react_test.channels import JobContextChannel


class BaseSignalTest(TestCase):

    def setUp(self):
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
        self.part2 = part2

        # Clear any pending broadcasts from setUp
        TransactionBroadcastManager._clear()

    def get_indexed_payload(self, send):
        """Get the mocked _relay calls indexed by (instance_type, id)"""
        indexed = {}
        keys = []
        for call in send.call_args_list:
            update = call.args[0]
            key = (update['_instance_type'], update['id'])
            indexed[key] = update
            keys.append(key)
        return indexed, keys


class AnchorSaveBroadcastTest(BaseSignalTest):
    """Test that saving the anchor model triggers a broadcast."""

    def test_anchor_save_triggers_broadcast(self):
        TransactionBroadcastManager._clear()
        with patch.object(JobContextChannel._signal_handler, '_relay') as send:
            self.job.name = 'Updated Job'
            self.job.save()
            TransactionBroadcastManager._flush()

            payload, keys = self.get_indexed_payload(send)
            job_key = ('react_test.serializers.JobNestedSerializer', self.job.id)
            self.assertIn(job_key, payload)

    def test_anchor_broadcast_has_correct_data(self):
        TransactionBroadcastManager._clear()
        with patch.object(JobContextChannel._signal_handler, '_relay') as send:
            self.job.name = 'New Name'
            self.job.save()
            TransactionBroadcastManager._flush()

            payload, _ = self.get_indexed_payload(send)
            job_data = payload[('react_test.serializers.JobNestedSerializer', self.job.id)]
            self.assertEqual(job_data['name'], 'New Name')
            self.assertEqual(job_data['id'], self.job.id)


class NestedModelBroadcastTest(BaseSignalTest):
    """Test that saving nested/related models triggers correct broadcasts."""

    def test_project_save_triggers_broadcast(self):
        """Saving a FK model (project) should broadcast it."""
        TransactionBroadcastManager._clear()
        with patch.object(JobContextChannel._signal_handler, '_relay') as send:
            self.project.name = 'Updated Project'
            self.project.save()
            TransactionBroadcastManager._flush()

            payload, keys = self.get_indexed_payload(send)
            proj_key = ('react_test.serializers.ProjectSerializer', self.project.id)
            self.assertIn(proj_key, payload)

    def test_user_save_triggers_broadcast(self):
        """Saving a deeply nested model (user via participant) should broadcast."""
        TransactionBroadcastManager._clear()
        with patch.object(JobContextChannel._signal_handler, '_relay') as send:
            self.user1.name = 'Updated User'
            self.user1.save()
            TransactionBroadcastManager._flush()

            payload, keys = self.get_indexed_payload(send)
            user_key = ('react_test.serializers.UserSerializer', self.user1.id)
            self.assertIn(user_key, payload)

    def test_participant_save_triggers_broadcast(self):
        """Saving a many-relation child (participant) should broadcast."""
        TransactionBroadcastManager._clear()
        with patch.object(JobContextChannel._signal_handler, '_relay') as send:
            self.part1.role = 'Manager'
            self.part1.save()
            TransactionBroadcastManager._flush()

            payload, keys = self.get_indexed_payload(send)
            part_key = ('react_test.serializers.ParticipantSerializer', self.part1.id)
            self.assertIn(part_key, payload)


class RelatedCreationPropagationTest(BaseSignalTest):
    """Test that creating new related instances triggers proper broadcasts.
    Adapted from gt-nx-react TrackingPointPropagationTest pattern."""

    def test_new_task_triggers_broadcast(self):
        """Creating a new task should broadcast the task."""
        TransactionBroadcastManager._clear()
        with patch.object(JobContextChannel._signal_handler, '_relay') as send:
            new_task = self.job.tasks.create(
                name="NewTask", developer=self.part1,
            )
            TransactionBroadcastManager._flush()

            payload, keys = self.get_indexed_payload(send)
            task_key = ('react_test.serializers.TaskSerializer', new_task.id)
            self.assertIn(task_key, payload)

    def test_new_asset_triggers_broadcast(self):
        """Creating a new asset should broadcast the asset."""
        TransactionBroadcastManager._clear()
        with patch.object(JobContextChannel._signal_handler, '_relay') as send:
            new_asset = self.job.asset_set.create(name="NewAsset")
            TransactionBroadcastManager._flush()

            payload, keys = self.get_indexed_payload(send)
            asset_key = ('react_test.serializers.AssetSerializer', new_asset.id)
            self.assertIn(asset_key, payload)


class DeduplicationTest(BaseSignalTest):
    """Test that multiple saves of the same instance are deduplicated."""

    def test_multiple_saves_produce_single_broadcast(self):
        TransactionBroadcastManager._clear()
        with patch.object(JobContextChannel._signal_handler, '_relay') as send:
            self.job.name = 'First'
            self.job.save()
            self.job.name = 'Second'
            self.job.save()
            self.job.name = 'Third'
            self.job.save()
            TransactionBroadcastManager._flush()

            payload, keys = self.get_indexed_payload(send)
            job_keys = [k for k in keys
                        if k[0] == 'react_test.serializers.JobNestedSerializer']
            self.assertEqual(len(job_keys), 1)

    def test_deduplicated_broadcast_has_final_state(self):
        TransactionBroadcastManager._clear()
        with patch.object(JobContextChannel._signal_handler, '_relay') as send:
            self.job.name = 'First'
            self.job.save()
            self.job.name = 'Final'
            self.job.save()
            TransactionBroadcastManager._flush()

            payload, _ = self.get_indexed_payload(send)
            job_data = payload[('react_test.serializers.JobNestedSerializer', self.job.id)]
            self.assertEqual(job_data['name'], 'Final')


class DeleteBroadcastTest(BaseSignalTest):
    """Test that deleting a model broadcasts the deletion."""

    def test_task_delete_triggers_broadcast(self):
        task = self.job.tasks.first()
        task_id = task.id
        TransactionBroadcastManager._clear()
        with patch.object(JobContextChannel._signal_handler, '_relay') as send:
            task.delete()
            TransactionBroadcastManager._flush()

            payload, keys = self.get_indexed_payload(send)
            task_key = ('react_test.serializers.TaskSerializer', task_id)
            self.assertIn(task_key, payload)
            self.assertTrue(payload[task_key].get('_deleted', False))

    def test_asset_delete_triggers_broadcast(self):
        asset = self.job.asset_set.first()
        asset_id = asset.id
        TransactionBroadcastManager._clear()
        with patch.object(JobContextChannel._signal_handler, '_relay') as send:
            asset.delete()
            TransactionBroadcastManager._flush()

            payload, keys = self.get_indexed_payload(send)
            asset_key = ('react_test.serializers.AssetSerializer', asset_id)
            self.assertIn(asset_key, payload)

    def test_asset_delete_updates_anchor_relationships(self):
        asset = self.job.asset_set.first()
        asset_id = asset.id
        TransactionBroadcastManager._clear()
        with patch.object(JobContextChannel._signal_handler, '_relay') as send:
            asset.delete()
            TransactionBroadcastManager._flush()

            payload, keys = self.get_indexed_payload(send)
            asset_key = ('react_test.serializers.AssetSerializer', asset_id)
            job_key = ('react_test.serializers.JobNestedSerializer', self.job.id)

            self.assertIn(asset_key, payload)
            self.assertTrue(payload[asset_key].get('_deleted', False))
            self.assertIn(job_key, payload)
            self.assertNotIn(asset_id, payload[job_key]['asset_set'])
