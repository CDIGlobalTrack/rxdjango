from django.core.checks import run_checks
from django.test import override_settings

import rxdjango.apps  # noqa: F401


def _warning_ids():
    return {warning.id for warning in run_checks()}


@override_settings(
    MONGO_URL='mongodb://localhost:27017/',
    REDIS_URL='redis://127.0.0.1:6379/0',
)
def test_check_service_auth_warns_for_unauthenticated_urls():
    warning_ids = _warning_ids()

    assert 'rxdjango.W001' in warning_ids
    assert 'rxdjango.W002' in warning_ids


@override_settings(
    MONGO_URL='mongodb://app-user:strong-password@localhost:27017/?authSource=admin',
    REDIS_URL='redis://:strong-password@127.0.0.1:6379/0',
)
def test_check_service_auth_accepts_authenticated_urls():
    warning_ids = _warning_ids()

    assert 'rxdjango.W001' not in warning_ids
    assert 'rxdjango.W002' not in warning_ids
