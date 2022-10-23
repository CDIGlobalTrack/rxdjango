from celery import shared_task
from redis import Redis
from django.conf import settings


@shared_task
def clean_transaction(queue):
    conn = Redis(host=settings.REDIS_HOST,
                 port=settings.REDIS_PORT,
                 db=settings.REDIS_DB)
    conn.delete(queue)
