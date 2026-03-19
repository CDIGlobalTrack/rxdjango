from channels.db import database_sync_to_async

from rxdjango.actions import action
from rxdjango.channels import ContextChannel
from rxdjango.operations import SAVE, CREATE, DELETE

from .models import Asset, Job, Participant, Task
from .serializers import JobNestedSerializer, TaskSerializer, AssetSerializer


class JobContextChannel(ContextChannel):

    class Meta:
        state = JobNestedSerializer()
        writable = {
            JobNestedSerializer: [SAVE],
            TaskSerializer: [SAVE, CREATE, DELETE],
            AssetSerializer: [SAVE, CREATE, DELETE],
        }

    @staticmethod
    def has_permission(user, **kwargs):
        return user.is_authenticated

    def can_save(self, instance, data):
        if isinstance(instance, Task):
            if 'forbidden_field' in data:
                return False
        return True

    def can_create(self, model_class, parent, data):
        if model_class == Task:
            if data.get('forbidden_field'):
                return False
        return True

    def can_delete(self, instance):
        if isinstance(instance, Asset):
            if instance.name.startswith('protected_'):
                return False
        return True

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

