"""
Tests for rxdjango.serialize module.

Tests the JSON serialization utilities including datetime conversion
and custom type handling.
"""
import json
from datetime import datetime
from decimal import Decimal

from pytz import utc

from rxdjango.serialize import default_serializer, json_dumps


class TestDefaultSerializer:
    """Tests for the default_serializer function."""

    def test_datetime_utc_conversion(self):
        """UTC datetime should be converted to ISO string with Z suffix."""
        dt = datetime(2024, 1, 15, 10, 30, 45, tzinfo=utc)
        result = default_serializer(dt)
        assert result.endswith('Z')
        assert '2024-01-15' in result
        assert '10:30:45' in result

    def test_datetime_strips_timezone_offset(self):
        """Datetime string should not contain + offset after normalization."""
        dt = datetime(2024, 6, 15, 12, 0, 0, tzinfo=utc)
        result = default_serializer(dt)
        assert '+' not in result

    def test_non_datetime_falls_back_to_str(self):
        """Non-datetime values should be converted via str()."""
        assert default_serializer(42) == '42'
        assert default_serializer(3.14) == '3.14'
        assert default_serializer(True) == 'True'
        assert default_serializer(None) == 'None'

    def test_decimal_falls_back_to_str(self):
        """Decimal values should be string-ified."""
        result = default_serializer(Decimal('99.99'))
        assert result == '99.99'


class TestJsonDumps:
    """Tests for the json_dumps wrapper."""

    def test_basic_dict(self):
        """Normal dicts should serialize as expected."""
        result = json_dumps({'key': 'value', 'num': 42})
        parsed = json.loads(result)
        assert parsed == {'key': 'value', 'num': 42}

    def test_datetime_in_dict(self):
        """Datetimes inside dicts should be auto-converted."""
        dt = datetime(2024, 3, 10, 8, 0, 0, tzinfo=utc)
        result = json_dumps({'created': dt})
        parsed = json.loads(result)
        assert parsed['created'].endswith('Z')

    def test_nested_structures(self):
        """Nested structures with non-standard types should serialize."""
        data = {
            'items': [1, 2, 3],
            'meta': {'count': Decimal('3')},
        }
        result = json_dumps(data)
        parsed = json.loads(result)
        assert parsed['items'] == [1, 2, 3]
        # Decimal serialized as string by default_serializer
        assert parsed['meta']['count'] == '3'

    def test_list_serialization(self):
        """Lists should serialize correctly."""
        result = json_dumps([1, 'two', 3.0])
        parsed = json.loads(result)
        assert parsed == [1, 'two', 3.0]

    def test_returns_string(self):
        """json_dumps should return a string."""
        result = json_dumps({'a': 1})
        assert isinstance(result, str)
