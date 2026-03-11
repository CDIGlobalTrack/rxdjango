import time
from django.test import TestCase
from rxdjango.state_model import StateModel
from react_test.models import User, Project, Participant, Job
from react_test.serializers import JobNestedSerializer


class StateModelTestCase(TestCase):

    def setUp(self):
        self.model = StateModel(JobNestedSerializer(), None)

        # Users
        user1 = User.objects.create(name="User1")
        user2 = User.objects.create(name="User2")
        shared_user = User.objects.create(name="SharedUser")

        # Project
        project1 = Project.objects.create(name="Project1")

        # Participants for project1
        participant1 = Participant.objects.create(project=project1, user=user1, name="Participant1", role="Developer")
        participant2 = Participant.objects.create(project=project1, user=user2, name="Participant2", role="Designer")
        shared_participant1 = Participant.objects.create(project=project1, user=shared_user, name="SharedParticipant", role="Tester")

        # Job
        job = Job.objects.create(project=project1, name="Job1_Project1")
        job2 = Job.objects.create(project=project1, name="Job2_Project1")

        # Tasks related to job1_project1
        job.tasks.create(name="Task1_Job1_Project1", developer=participant1)
        job.tasks.create(name="Task2_Job1_Project1", developer=participant2)
        job.tasks.create(name="Task3_Job1_Project1", developer=shared_participant1)

        job.asset_set.create(name="Asset1_Job1")

        job.deadline_set.create(name="Deadline_1")
        job2.deadline_set.create(name="Deadline_2")

        self.job = job

    def get_anchors(self, serialized):
        return list(self.model.get_anchors(serialized))

    def test_get_anchors_for_anchor(self):
        serialized = self.model.serialize_instance(self.job, time.time())
        anchors = self.get_anchors(serialized)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0], self.job)

    def test_get_anchors_for_foreign_key(self):
        project_model = self.model['project']
        serialized = project_model.serialize_instance(self.job.project, time.time())
        anchors = self.get_anchors(serialized)
        self.assertEqual(len(anchors), 2)
        self.assertTrue(self.job in anchors)

    def test_get_anchors_for_related_with_related_name(self):
        task_model = self.model['tasks']
        serialized = task_model.serialize_instance(
            self.job.tasks.first(),
            time.time(),
        )
        anchors = self.get_anchors(serialized)
        self.assertEqual(len(anchors), 1)
        self.assertTrue(self.job in anchors)

    def test_get_anchors_for_related_with_related_name_as_same(self):
        deadline_model = self.model['deadline_set']
        serialized = deadline_model.serialize_instance(
            self.job.deadline_set.first(),
            time.time(),
        )
        anchors = self.get_anchors(serialized)
        self.assertEqual(len(anchors), 1)
        self.assertTrue(self.job in anchors)

    def test_get_anchors_for_related_without_related_name(self):
        asset_model = self.model['asset_set']
        serialized = asset_model.serialize_instance(
            self.job.asset_set.first(),
            time.time(),
        )
        anchors = self.get_anchors(serialized)
        self.assertEqual(len(anchors), 1)
        self.assertTrue(self.job in anchors)


class StateModelStructureTestCase(TestCase):
    """Tests for StateModel structural properties: query_path, instance_path,
    anchor_key, and instance_type.

    Verifies that recursive serializer introspection builds the correct
    dependency tree for navigating from any nested instance back to anchors.
    """

    def setUp(self):
        self.model = StateModel(JobNestedSerializer(), None)

    # -- anchor (root) --

    def test_anchor_query_path(self):
        self.assertEqual(self.model.query_path, [])

    def test_anchor_instance_path(self):
        self.assertEqual(self.model.instance_path, [])

    def test_anchor_key_is_pk(self):
        self.assertEqual(self.model.anchor_key, 'id')

    def test_anchor_instance_type(self):
        self.assertEqual(
            self.model.instance_type,
            'react_test.serializers.JobNestedSerializer',
        )

    # -- project (ForwardManyToOne / FK) --

    def test_project_query_path(self):
        self.assertEqual(self.model['project'].query_path, ['project'])

    def test_project_instance_path(self):
        self.assertEqual(self.model['project'].instance_path, ['project'])

    def test_project_anchor_key(self):
        self.assertEqual(self.model['project'].anchor_key, 'project')

    def test_project_instance_type(self):
        self.assertEqual(
            self.model['project'].instance_type,
            'react_test.serializers.ProjectSerializer',
        )

    def test_project_not_many(self):
        self.assertFalse(self.model['project'].many)

    # -- tasks (ReverseManyToOne with related_name='tasks') --

    def test_tasks_query_path(self):
        self.assertEqual(self.model['tasks'].query_path, ['tasks'])

    def test_tasks_instance_path(self):
        self.assertEqual(self.model['tasks'].instance_path, ['tasks'])

    def test_tasks_anchor_key(self):
        self.assertEqual(self.model['tasks'].anchor_key, 'tasks')

    def test_tasks_is_many(self):
        self.assertTrue(self.model['tasks'].many)

    def test_tasks_instance_type(self):
        self.assertEqual(
            self.model['tasks'].instance_type,
            'react_test.serializers.TaskSerializer',
        )

    # -- asset_set (ReverseManyToOne without explicit related_name) --

    def test_asset_set_query_path(self):
        # ReverseManyToOne uses related_query_name(), which is 'asset' (model name lowercase)
        self.assertEqual(self.model['asset_set'].query_path, ['asset'])

    def test_asset_set_instance_path(self):
        self.assertEqual(self.model['asset_set'].instance_path, ['asset_set'])

    def test_asset_set_is_many(self):
        self.assertTrue(self.model['asset_set'].many)

    # -- deadline_set (ReverseManyToOne with related_name='deadline_set') --

    def test_deadline_set_query_path(self):
        self.assertEqual(self.model['deadline_set'].query_path, ['deadline_set'])

    def test_deadline_set_is_many(self):
        self.assertTrue(self.model['deadline_set'].many)

    # -- nested: tasks -> developer (FK from Task to Participant) --

    def test_task_developer_query_path(self):
        self.assertEqual(
            self.model['tasks']['developer'].query_path,
            ['tasks', 'developer'],
        )

    def test_task_developer_instance_path(self):
        self.assertEqual(
            self.model['tasks']['developer'].instance_path,
            ['tasks', 'developer'],
        )

    def test_task_developer_anchor_key(self):
        self.assertEqual(
            self.model['tasks']['developer'].anchor_key,
            'tasks__developer',
        )

    def test_task_developer_not_many(self):
        self.assertFalse(self.model['tasks']['developer'].many)

    # -- nested: project -> participant_set (ReverseManyToOne) --

    def test_project_participant_set_query_path(self):
        # ReverseManyToOne uses related_query_name() -> 'participant'
        self.assertEqual(
            self.model['project']['participant_set'].query_path,
            ['project', 'participant'],
        )

    def test_project_participant_set_anchor_key(self):
        self.assertEqual(
            self.model['project']['participant_set'].anchor_key,
            'project__participant',
        )

    def test_project_participant_set_is_many(self):
        self.assertTrue(self.model['project']['participant_set'].many)

    # -- deeply nested: project -> participant_set -> user --

    def test_project_participant_user_query_path(self):
        self.assertEqual(
            self.model['project']['participant_set']['user'].query_path,
            ['project', 'participant', 'user'],
        )

    def test_project_participant_user_anchor_key(self):
        self.assertEqual(
            self.model['project']['participant_set']['user'].anchor_key,
            'project__participant__user',
        )

    def test_project_participant_user_not_many(self):
        self.assertFalse(self.model['project']['participant_set']['user'].many)

    # -- tasks -> developer -> user (3-level nesting) --

    def test_task_developer_user_query_path(self):
        self.assertEqual(
            self.model['tasks']['developer']['user'].query_path,
            ['tasks', 'developer', 'user'],
        )

    def test_task_developer_user_anchor_key(self):
        self.assertEqual(
            self.model['tasks']['developer']['user'].anchor_key,
            'tasks__developer__user',
        )

    # -- children enumeration --

    def test_anchor_children_keys(self):
        self.assertEqual(
            set(self.model.children.keys()),
            {'project', 'tasks', 'asset_set', 'deadline_set'},
        )

    def test_tasks_children_keys(self):
        self.assertEqual(
            set(self.model['tasks'].children.keys()),
            {'developer'},
        )

    def test_project_children_keys(self):
        self.assertEqual(
            set(self.model['project'].children.keys()),
            {'participant_set'},
        )

    def test_asset_set_has_no_children(self):
        self.assertEqual(self.model['asset_set'].children, {})

    def test_deadline_set_has_no_children(self):
        self.assertEqual(self.model['deadline_set'].children, {})
