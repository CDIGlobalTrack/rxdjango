from rest_framework import serializers
from .models import User, Participant, Project, Task, Asset, Job, Deadline

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            '__all__',
            ]

class ParticipantSerializer(serializers.ModelSerializer):
    user = UserSerializer()

    class Meta:
        model = Participant
        fields = "__all__"

class ProjectSerializer(serializers.ModelSerializer):
    participant_set = ParticipantSerializer(many=True)

    class Meta:
        model = Project
        fields = ['id', 'name', 'participant_set']


class TaskSerializer(serializers.ModelSerializer):
    developer = ParticipantSerializer()

    class Meta:
        model = Task
        fields = "__all__"


class AssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Asset
        fields = '__all__'


class DeadlineSerializer(serializers.ModelSerializer):
    class Meta:
        model = Deadline
        fields = '__all__'


class JobNestedSerializer(serializers.ModelSerializer):
    project = ProjectSerializer()
    tasks = TaskSerializer(many=True)
    asset_set = AssetSerializer(many=True)
    deadline_set = DeadlineSerializer(many=True)

    class Meta:
        model = Job
        fields = ['id', 'project', 'name', 'tasks', 'asset_set', 'deadline_set']
