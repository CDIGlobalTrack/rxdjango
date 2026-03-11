"""
Tests for the _adapt function in rxdjango.mongo module.

Tests the type conversion from Python types to MongoDB-compatible values.
"""
from datetime import datetime
from decimal import Decimal

import pytest
from pytz import utc

from rxdjango.mongo import _adapt


class TestAdapt:
    """Tests for the _adapt function."""

    def test_decimal_to_float(self):
        """Decimal values should be converted to float."""
        result = _adapt({'price': Decimal('19.99')})
        assert isinstance(result['price'], float)
        assert result['price'] == pytest.approx(19.99)

    def test_datetime_to_iso_string(self):
        """Datetime values should be converted to ISO string with Z suffix."""
        dt = datetime(2024, 3, 10, 12, 30, 45, 123456, tzinfo=utc)
        result = _adapt({'created': dt})
        assert isinstance(result['created'], str)
        assert result['created'].endswith('Z')
        assert '2024-03-10' in result['created']

    def test_datetime_truncated_to_microseconds(self):
        """Datetime ISO string should be truncated to 26 chars before Z."""
        dt = datetime(2024, 1, 1, 0, 0, 0, 0, tzinfo=utc)
        result = _adapt({'ts': dt})
        # Format: YYYY-MM-DDTHH:MM:SS.ffffff -> 26 chars + Z
        iso_part = result['ts'][:-1]  # Remove Z
        assert len(iso_part) == 26
        assert result['ts'].endswith('Z')

    def test_regular_values_unchanged(self):
        """Non-special types should pass through unchanged."""
        data = {
            'name': 'test',
            'count': 42,
            'ratio': 3.14,
            'active': True,
            'empty': None,
        }
        result = _adapt(data)
        assert result['name'] == 'test'
        assert result['count'] == 42
        assert result['ratio'] == 3.14
        assert result['active'] is True
        assert result['empty'] is None

    def test_returns_new_dict(self):
        """_adapt should return a new dict, not modify the original."""
        original = {'price': Decimal('10.00')}
        result = _adapt(original)
        assert result is not original
        # Original should still have Decimal
        assert isinstance(original['price'], Decimal)

    def test_mixed_types(self):
        """Should handle a dict with multiple types correctly."""
        data = {
            'id': 1,
            'name': 'widget',
            'price': Decimal('29.99'),
            'created': datetime(2024, 6, 15, 0, 0, tzinfo=utc),
            'tags': ['a', 'b'],
        }
        result = _adapt(data)
        assert isinstance(result['price'], float)
        assert isinstance(result['created'], str)
        assert result['id'] == 1
        assert result['name'] == 'widget'
        assert result['tags'] == ['a', 'b']

    def test_datetime_converted_to_utc(self):
        """Timezone-aware datetimes should be normalized to UTC."""
        dt = datetime.fromisoformat('2024-06-15T08:30:00+02:00')
        result = _adapt({'created': dt})
        assert result['created'] == '2024-06-15T06:30:00.000000Z'

    def test_empty_dict(self):
        """Empty dict should return empty dict."""
        assert _adapt({}) == {}

    def test_preserves_underscore_fields(self):
        """Metadata fields starting with _ should be preserved."""
        data = {
            '_instance_type': 'app.Serializer',
            '_tstamp': 12345.0,
            '_operation': 'update',
            'id': 1,
        }
        result = _adapt(data)
        assert result['_instance_type'] == 'app.Serializer'
        assert result['_tstamp'] == 12345.0
