# chat_demo/models.py

from django.db import models
from users.models import User

class Room(models.Model):
    title = models.CharField(max_length=255)
    participants = models.ManyToManyField(User, related_name='rooms')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title

class Message(models.Model):
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    author = models.ForeignKey(User, related_name='messages', on_delete=models.CASCADE)
    room = models.ForeignKey(Room, related_name='messages', on_delete=models.CASCADE)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"{self.author.login}: {self.content[:50]}..."

class MessageView(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    message = models.ForeignKey(Message, on_delete=models.CASCADE)
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'message',)
        ordering = ['viewed_at']

    def __str__(self):
        return f"{self.user.login} viewed {self.message.author.login}'s message at {self.viewed_at}"
