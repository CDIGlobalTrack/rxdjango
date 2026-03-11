import asyncio
from unittest.mock import AsyncMock, patch

from django.test import TransactionTestCase
from rxdjango.state_loader import StateLoader
from rxdjango.redis import RedisSession, RedisStateSession
from rxdjango.mongo import MongoSignalWriter, MongoStateSession
from react_test.models import User, Project, Participant, Job
from react_test.channels import JobContextChannel


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
        Job.objects.create(project=project1, name="Job2_Project1")

        # Tasks related to job1_project1
        job.tasks.create(name="Task1_Job1_Project1", developer=participant1)
        job.tasks.create(name="Task2_Job1_Project1", developer=participant2)
        job.tasks.create(name="Task3_Job1_Project1", developer=shared_participant1)

        self.job = job
        self.user = self.job.project.participant_set.first().user

        self.channel = JobContextChannel(anchor_id=self.job.id, user=self.user)
        asyncio.run(self.channel.initialize_anchors())

        RedisSession.init_database(JobContextChannel)
        MongoSignalWriter(JobContextChannel).init_database()

    @property
    def anchor_id(self):
        return self.channel.anchor_ids[0]

    async def test_first_client_gets_cold(self):
        await self._test_state('cold')

    async def _collect_instances(self, loader):
        instances = []
        async for batch in loader.list_instances():
            instances += batch
        return instances

    async def _test_state(self, state):
        async with StateLoader(self.channel, self.anchor_id) as loader:
            instances = await self._collect_instances(loader)
        self.assertEqual(len(instances), 17)
        self.assertEqual(instances[0]['_cache_state'], state)
        return instances

    async def _cache_state(self):
        redis = RedisStateSession(self.channel, self.anchor_id)
        await redis.connect()
        state = await redis._conn.get(redis.state)
        return 0 if state is None else int(state)

    async def test_second_client_gets_heating(self):
        async with StateLoader(self.channel, self.anchor_id) as cold_loader:
            async with StateLoader(self.channel, self.anchor_id) as heating_loader:
                heating_iterator = heating_loader.list_instances().__aiter__()
                async for cold_batch in cold_loader.list_instances():
                    heating_batch = await heating_iterator.__anext__()
                    self.assertEqual(cold_batch[0].pop('_cache_state'), 'cold')
                    self.assertEqual(heating_batch[0].pop('_cache_state'), 'heating')
                    self.assertEqual(cold_batch, heating_batch)

    async def test_further_clients_get_heating(self):
        async with StateLoader(self.channel, self.anchor_id) as cold_loader:
            heating = [StateLoader(self.channel, self.anchor_id) for i in range(5)]
            heating_iterators = []
            try:
                for loader in heating:
                    await loader.__aenter__()
                    heating_iterators.append(loader.list_instances().__aiter__())

                async for cold_batch in cold_loader.list_instances():
                    self.assertEqual(cold_batch[0].pop('_cache_state'), 'cold')
                    for heating_iterator in heating_iterators:
                        heating_batch = await heating_iterator.__anext__()
                        self.assertEqual(heating_batch[0].pop('_cache_state'), 'heating')
                        self.assertEqual(cold_batch, heating_batch)
            finally:
                for loader in reversed(heating):
                    await loader.__aexit__(None, None, None)

    async def test_hot_state_after_first_client(self):
        await self._test_state('cold')
        await self._test_state('hot')

    async def test_hot_state_after_bunch_of_clients(self):
        await self.test_further_clients_get_heating()
        await self._test_state('hot')

    async def test_clear_cache_transitions_hot_to_cold(self):
        await self._test_state('cold')
        self.assertEqual(await self._cache_state(), 2)

        cleared = await JobContextChannel.clear_cache(self.anchor_id)

        self.assertTrue(cleared)
        self.assertEqual(await self._cache_state(), 0)

        await self._test_state('cold')

    async def test_connect_during_cooling_transitions_to_heating_then_hot(self):
        await self._test_state('cold')
        self.assertEqual(await self._cache_state(), 2)

        redis = RedisStateSession(self.channel, self.anchor_id)
        self.assertTrue(await redis.start_cooling())
        self.assertEqual(await self._cache_state(), 3)

        all_instances = []
        async for batch in MongoStateSession.list_and_clear_instances(
            JobContextChannel,
            self.anchor_id,
        ):
            all_instances.extend(batch)
            await redis.write_instances(batch)

        loader = StateLoader(self.channel, self.anchor_id)
        await loader.__aenter__()
        try:
            self.assertEqual(loader.cache_state, 1)
            self.assertEqual(await self._cache_state(), 1)

            result = await redis.finish_cooling()
            self.assertEqual(result, 1)

            mongo = JobContextChannel._create_mongo_writer(self.anchor_id)
            await mongo.write_instances(all_instances)
            await redis.end_cold_session(success=True)

            instances = await self._collect_instances(loader)
            self.assertEqual(len(instances), len(all_instances))
            self.assertEqual(instances[0]['_cache_state'], 'heating')
        finally:
            await loader.__aexit__(None, None, None)

        self.assertEqual(await self._cache_state(), 2)
        await self._test_state('hot')

    async def test_failed_cold_load_rolls_back_to_cold(self):
        loader = StateLoader(self.channel, self.anchor_id)

        with self.assertRaises(RuntimeError):
            async with loader:
                with patch.object(
                    loader,
                    '_get_anchor_from_db',
                    new=AsyncMock(side_effect=RuntimeError('boom')),
                ):
                    await self._collect_instances(loader)

        self.assertEqual(await self._cache_state(), 0)
        await self._test_state('cold')
