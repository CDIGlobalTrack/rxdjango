from django.db import models

class User(models.Model):
    name = models.CharField(max_length=64)

class Project(models.Model):
    name = models.CharField(max_length=64)

class Participant(models.Model):
    project = models.ForeignKey(Project,
                                on_delete=models.CASCADE)
    user = models.ForeignKey(User,
                             on_delete=models.CASCADE)
    name = models.CharField(max_length=64)
    role = models.CharField(max_length=64)

class Job(models.Model):
    project = models.ForeignKey(Project,
                                on_delete=models.CASCADE)
    name = models.CharField(max_length=64)

class Task(models.Model):
    job = models.ForeignKey(Job,
                            on_delete=models.CASCADE,
                            related_name='tasks',
                            )
    name = models.CharField(max_length=64)
    developer = models.ForeignKey(Participant,
                                  on_delete=models.CASCADE)

class Asset(models.Model):
    job = models.ForeignKey(Job,
                            on_delete=models.CASCADE,
                            )
    name = models.CharField(max_length=64)


class Deadline(models.Model):
    job = models.ForeignKey(Job,
                            related_name='deadline_set',
                            on_delete=models.CASCADE,
                            )
    name = models.CharField(max_length=64)
