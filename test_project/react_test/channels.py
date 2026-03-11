from channels.db import database_sync_to_async

from rxdjango.actions import action
from rxdjango.channels import ContextChannel

from .models import Asset, Job, Participant
from .serializers import JobNestedSerializer


class JobContextChannel(ContextChannel):

    class Meta:
        state = JobNestedSerializer()

    @staticmethod
    def has_permission(user, **kwargs):
        return user.is_authenticated

    @action
    async def rename_job(self, name: str) -> dict:
        return await database_sync_to_async(self._rename_job)(name)

    def _rename_job(self, name: str) -> dict:
        job = Job.objects.get(pk=self.kwargs['job_id'])
        job.name = name
        job.save()
        return {
            'jobId': job.id,
            'name': job.name,
        }

    @action
    async def create_task(self, name: str, developer_id: int) -> dict:
        return await database_sync_to_async(self._create_task)(name, developer_id)

    def _create_task(self, name: str, developer_id: int) -> dict:
        job = Job.objects.get(pk=self.kwargs['job_id'])
        developer = Participant.objects.get(pk=developer_id)
        task = job.tasks.create(name=name, developer=developer)
        return {
            'taskId': task.id,
            'name': task.name,
            'developerId': developer.id,
        }

    @action
    async def rename_asset(self, asset_id: int, name: str) -> dict:
        return await database_sync_to_async(self._rename_asset)(asset_id, name)

    def _rename_asset(self, asset_id: int, name: str) -> dict:
        asset = Asset.objects.get(pk=asset_id, job_id=self.kwargs['job_id'])
        asset.name = name
        asset.save()
        return {
            'assetId': asset_id,
            'name': asset.name,
        }

    @action
    async def delete_asset(self, asset_id: int) -> dict:
        return await database_sync_to_async(self._delete_asset)(asset_id)

    def _delete_asset(self, asset_id: int) -> dict:
        asset = Asset.objects.get(pk=asset_id, job_id=self.kwargs['job_id'])
        asset.delete()
        return {
            'assetId': asset_id,
            'deleted': True,
        }
