"""
Pytest configuration for rxdjango unit tests.

These tests focus on pure Python logic that does NOT require Django,
Redis, MongoDB, or any external services. Django-dependent tests
live in test_project/react_test/tests/ and are run via manage.py test.

For tests that need minimal Django setup (e.g. testing metaclass logic
with serializers), we configure Django settings here.
"""
import os
import django
from django.conf import settings


def pytest_configure():
    """Set up minimal Django settings for unit tests."""
    if not settings.configured:
        settings.configure(
            INSTALLED_APPS=[
                'django.contrib.contenttypes',
                'django.contrib.auth',
                'rest_framework',
                'rest_framework.authtoken',
                'channels',
            ],
            DATABASES={
                'default': {
                    'ENGINE': 'django.db.backends.sqlite3',
                    'NAME': ':memory:',
                }
            },
            CHANNEL_LAYERS={
                'default': {
                    'BACKEND': 'channels.layers.InMemoryChannelLayer',
                },
            },
            REDIS_URL='redis://localhost:6379/15',
            MONGO_URL='mongodb://localhost:27017/',
            MONGO_STATE_DB='rxdjango_test_unit',
            DEFAULT_AUTO_FIELD='django.db.models.BigAutoField',
            SECRET_KEY='test-secret-key',
            USE_TZ=True,
        )
        django.setup()
