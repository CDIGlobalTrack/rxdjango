from django.test import TransactionTestCase
from rxdjango.state_loader import StateLoader
from rxdjango.redis import RedisSession
from rxdjango.mongo import MongoSession
from react_test.models import User, Project, Participant, Job, Task
from react_test.serializers import JobNestedSerializer
from react_test.channels import JobStateChannel


class StateLoaderTestCase(TransactionTestCase):

    def setUp(self):
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

        self.job = job
        self.user = self.job.project.participant_set.first().user

        self.channel = JobStateChannel(anchor_id=self.job.id, user=self.user)

        RedisSession.init_database(JobStateChannel)
        MongoSession.init_database(JobStateChannel)

    async def test_first_client_gets_cold(self):
        await self._test_state('cold')

    async def _test_state(self, state):
        instances = []
        async with StateLoader(self.channel) as loader:
            async for batch in loader.list_instances():
                instances += batch

        self.assertEqual(len(instances), 8)
        self.assertEqual(instances[0]['_cache_state'], state)
        return instances

    async def test_second_client_gets_heating(self):
        async with StateLoader(self.channel) as cold_loader:
            async with StateLoader(self.channel) as heating_loader:
                heating_iterator = heating_loader.list_instances().__aiter__()
                async for cold_batch in cold_loader.list_instances():
                    heating_batch = await heating_iterator.__anext__()
                    self.assertEqual(cold_batch[0].pop('_cache_state'), 'cold')
                    self.assertEqual(heating_batch[0].pop('_cache_state'), 'heating')
                    self.assertEqual(cold_batch, heating_batch)

    async def test_further_clients_get_heating(self):
        async with StateLoader(self.channel) as cold_loader:
            heating = [ StateLoader(self.channel) for i in range(5) ]
            heating_iterators = []
            for loader in heating:
                await loader.__aenter__()
                heating_iterators.append(loader.list_instances().__aiter__())

            async for cold_batch in cold_loader.list_instances():
                self.assertEqual(cold_batch[0].pop('_cache_state'), 'cold')
                for heating_iterator in heating_iterators:
                    heating_batch = await heating_iterator.__anext__()
                    self.assertEqual(heating_batch[0].pop('_cache_state'), 'heating')
                    self.assertEqual(cold_batch, heating_batch)

    async def test_hot_state_after_first_client(self):
        await self._test_state('cold')
        await self._test_state('hot')

    async def test_hot_state_after_bunch_of_clients(self):
        await self.test_further_clients_get_heating()
        await self._test_state('hot')
