from rest_framework import serializers
from .models import Room, Message, MessageView
from users.serializers import UserSerializer


class MessageSerializer(serializers.ModelSerializer):
    author = UserSerializer()

    class Meta:
        model = Message
        fields = ('id', 'content', 'timestamp', 'author', 'room')


class MessageViewSerializer(serializers.ModelSerializer):
    user = UserSerializer()
    message = MessageSerializer()

    class Meta:
        model = MessageView
        fields = ('id', 'user', 'message', 'viewed_at')


class RoomSerializer(serializers.ModelSerializer):
    participants = UserSerializer(many=True)
    messages = MessageSerializer(many=True)

    class Meta:
        model = Room
        fields = ('id', 'title', 'participants', 'created_at', 'messages')
