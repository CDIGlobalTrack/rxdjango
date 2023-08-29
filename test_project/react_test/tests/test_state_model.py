import time
from django.test import TestCase
from react_framework.state_model import StateModel
from react_test.models import User, Project, Participant, Job, Task
from react_test.serializers import JobNestedSerializer


class StateModelTestCase(TestCase):

    def setUp(self):
        self.model = StateModel(JobNestedSerializer())

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

    def test_get_anchors_for_anchor(self):
        serialized = self.model.serialize_instance(self.job, time.time())
        anchors = self.model.get_anchors(serialized)
        self.assertEqual(len(anchors), 1)
        self.assertEqual(anchors[0], self.job)

    def test_get_anchors_for_foreign_key(self):
        project_model = self.model['project']
        serialized = project_model.serialize_instance(self.job.project, time.time())
        anchors = self.model.get_anchors(serialized)
        self.assertEqual(len(anchors), 2)
        self.assertTrue(self.job in anchors)

    def test_get_anchors_for_related_with_related_name(self):
        task_model = self.model['tasks']
        serialized = task_model.serialize_instance(
            self.job.tasks.first(),
            time.time(),
        )
        anchors = self.model.get_anchors(serialized)
        self.assertEqual(len(anchors), 1)
        self.assertTrue(self.job in anchors)

    def test_get_anchors_for_related_with_related_name_as_same(self):
        deadline_model = self.model['deadline_set']
        serialized = deadline_model.serialize_instance(
            self.job.deadline_set.first(),
            time.time(),
        )
        anchors = self.model.get_anchors(serialized)
        self.assertEqual(len(anchors), 1)
        self.assertTrue(self.job in anchors)

    def test_get_anchors_for_related_without_related_name(self):
        asset_model = self.model['asset_set']
        serialized = asset_model.serialize_instance(
            self.job.asset_set.first(),
            time.time(),
        )
        anchors = self.model.get_anchors(serialized)
        self.assertEqual(len(anchors), 1)
        self.assertTrue(self.job in anchors)
