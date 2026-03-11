"""
Tests for rxdjango.ts module helpers.

Tests the TypeScript generation helper functions: interface naming,
type mapping, export registry, snake_to_camel, and header generation.
"""
from decimal import Decimal
from datetime import datetime

from rxdjango.ts import (
    interface_name,
    export_interface,
    ts_exported,
    _key,
    get_ts_type,
    snake_to_camel,
    TYPEMAP,
)


class TestInterfaceName:
    """Tests for the interface_name function."""

    def test_removes_serializer_suffix(self):
        """'Serializer' should be replaced with 'Type'."""
        class UserSerializer:
            __name__ = 'UserSerializer'

        assert interface_name(UserSerializer) == 'UserType'

    def test_no_serializer_suffix(self):
        """If no 'Serializer' suffix, class name is unchanged + 'Type' appended."""
        class MyClass:
            __name__ = 'MyClass'

        # replace('Serializer', 'Type') does nothing if not present
        assert interface_name(MyClass) == 'MyClass'

    def test_nested_serializer_name(self):
        class ProjectNestedSerializer:
            __name__ = 'ProjectNestedSerializer'

        # Only the 'Serializer' part is replaced
        assert interface_name(ProjectNestedSerializer) == 'ProjectNestedType'


class TestKey:
    """Tests for the _key function."""

    def test_generates_module_dot_name(self):
        class TestSer:
            __module__ = 'myapp.serializers'
            __name__ = 'TestSer'

        assert _key(TestSer) == 'myapp.serializers.TestSer'


class TestExportRegistry:
    """Tests for export_interface and ts_exported."""

    def test_exported_after_registration(self):
        """ts_exported should return True after export_interface."""
        from rest_framework import serializers

        class ExportedSerializer(serializers.Serializer):
            __module__ = 'test_export.serializers'
            __name__ = 'ExportedSerializer'

        export_interface(ExportedSerializer)
        assert ts_exported(ExportedSerializer) is True

    def test_not_exported_without_registration(self):
        """ts_exported should return False for non-registered serializers."""
        class NotExportedSerializer:
            __module__ = 'test_noexport.serializers'
            __name__ = 'NotExportedSerializer'

        assert ts_exported(NotExportedSerializer) is False


class TestGetTsType:
    """Tests for the get_ts_type function."""

    def test_int_maps_to_number(self):
        assert get_ts_type(int) == 'number'

    def test_float_maps_to_number(self):
        assert get_ts_type(float) == 'number'

    def test_str_maps_to_string(self):
        assert get_ts_type(str) == 'string'

    def test_bool_maps_to_boolean(self):
        assert get_ts_type(bool) == 'boolean'

    def test_datetime_maps_to_string(self):
        assert get_ts_type(datetime) == 'string'

    def test_decimal_maps_to_number(self):
        assert get_ts_type(Decimal) == 'number'

    def test_none_type_maps_to_null(self):
        assert get_ts_type(type(None)) == 'null'

    def test_union_type(self):
        """Union types should produce pipe-separated TS types."""
        # Python 3.10+ union syntax
        union = int | str
        result = get_ts_type(union)
        assert 'number' in result
        assert 'string' in result
        assert '|' in result

    def test_optional_type(self):
        """int | None should map to 'number | null'."""
        union = int | None
        result = get_ts_type(union)
        assert 'number' in result
        assert 'null' in result


class TestSnakeToCamel:
    """Tests for snake_to_camel function."""

    def test_simple_snake_case(self):
        assert snake_to_camel('my_method') == 'myMethod'

    def test_multiple_underscores(self):
        assert snake_to_camel('get_user_name') == 'getUserName'

    def test_already_camel(self):
        assert snake_to_camel('myMethod') == 'myMethod'

    def test_single_word(self):
        assert snake_to_camel('name') == 'name'

    def test_leading_lowercase_preserved(self):
        assert snake_to_camel('set_value') == 'setValue'


class TestTypemap:
    """Tests for the TYPEMAP constant."""

    def test_all_python_types_mapped(self):
        """All expected Python types should be in TYPEMAP."""
        expected_keys = {int, float, Decimal, datetime, str, bool, type(None)}
        assert expected_keys.issubset(set(TYPEMAP.keys()))

    def test_no_empty_values(self):
        """All mapped TS types should be non-empty strings."""
        for key, value in TYPEMAP.items():
            assert isinstance(value, str)
            assert len(value) > 0
