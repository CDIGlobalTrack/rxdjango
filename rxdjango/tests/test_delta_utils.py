"""
Tests for rxdjango.utils.delta_utils module.

Tests the delta computation logic that minimizes WebSocket broadcast payloads
by comparing old and new instance state.
"""
import pytest
from rxdjango.utils.delta_utils import generate_delta


class TestGenerateDelta:
    """Tests for the generate_delta function."""

    def test_no_changes_returns_empty(self):
        """If nothing changed, should return empty list."""
        original = {'id': 1, 'name': 'foo', 'status': 'active'}
        instance = {'id': 1, 'name': 'foo', 'status': 'active'}
        result = generate_delta(original, instance)
        assert result == []

    def test_changed_field_returns_instance(self):
        """If a field changed, should return list with the instance."""
        original = {'id': 1, 'name': 'foo', 'status': 'active'}
        instance = {'id': 1, 'name': 'bar', 'status': 'active'}
        result = generate_delta(original, instance)
        assert len(result) == 1
        assert result[0]['name'] == 'bar'

    def test_unchanged_fields_removed_from_instance(self):
        """Unchanged fields should be removed from the instance dict."""
        original = {'id': 1, 'name': 'foo', 'status': 'active', 'count': 5}
        instance = {'id': 1, 'name': 'bar', 'status': 'active', 'count': 5}
        generate_delta(original, instance)
        # 'status' and 'count' were same, should be removed
        assert 'status' not in instance
        assert 'count' not in instance
        # 'name' changed, should remain
        assert 'name' in instance

    def test_id_field_preserved(self):
        """The 'id' field should never be removed."""
        original = {'id': 1, 'name': 'foo'}
        instance = {'id': 1, 'name': 'bar'}
        result = generate_delta(original, instance)
        assert result[0]['id'] == 1

    def test_underscore_fields_preserved(self):
        """Fields starting with _ should be skipped (not removed)."""
        original = {'id': 1, '_instance_type': 'test.Ser', 'name': 'foo'}
        instance = {'id': 1, '_instance_type': 'test.Ser', 'name': 'bar'}
        result = generate_delta(original, instance)
        assert result[0]['_instance_type'] == 'test.Ser'

    def test_multiple_changes(self):
        """Multiple changed fields should all be present."""
        original = {'id': 1, 'name': 'foo', 'status': 'active', 'count': 0}
        instance = {'id': 1, 'name': 'bar', 'status': 'inactive', 'count': 0}
        result = generate_delta(original, instance)
        assert len(result) == 1
        assert result[0]['name'] == 'bar'
        assert result[0]['status'] == 'inactive'

    def test_missing_key_in_instance_skipped(self):
        """If instance is missing a key from original, skip it."""
        original = {'id': 1, 'name': 'foo', 'extra': 'val'}
        instance = {'id': 1, 'name': 'foo'}
        # Should not raise, just skip the missing key
        result = generate_delta(original, instance)
        assert result == []

    def test_all_fields_changed(self):
        """If all non-special fields changed, all remain in instance."""
        original = {'id': 1, 'a': 1, 'b': 2, 'c': 3}
        instance = {'id': 1, 'a': 10, 'b': 20, 'c': 30}
        result = generate_delta(original, instance)
        assert len(result) == 1
        assert result[0]['a'] == 10
        assert result[0]['b'] == 20
        assert result[0]['c'] == 30

    def test_type_mismatch_counts_as_change(self):
        """Different types for same key should be a change."""
        original = {'id': 1, 'count': '5'}
        instance = {'id': 1, 'count': 5}
        result = generate_delta(original, instance)
        assert len(result) == 1

    def test_none_to_value_is_change(self):
        """Changing from None to a value should be detected."""
        original = {'id': 1, 'name': None}
        instance = {'id': 1, 'name': 'hello'}
        result = generate_delta(original, instance)
        assert len(result) == 1
        assert result[0]['name'] == 'hello'

    def test_value_to_none_is_change(self):
        """Changing from a value to None should be detected."""
        original = {'id': 1, 'name': 'hello'}
        instance = {'id': 1, 'name': None}
        result = generate_delta(original, instance)
        assert len(result) == 1
        assert result[0]['name'] is None

    def test_modifies_instance_in_place(self):
        """generate_delta modifies the instance dict in place."""
        original = {'id': 1, 'a': 1, 'b': 2}
        instance = {'id': 1, 'a': 1, 'b': 3}
        generate_delta(original, instance)
        # 'a' was same, removed in-place
        assert 'a' not in instance
        # 'b' changed, still there
        assert 'b' in instance


class TestGenerateDeltaCExtension:
    """Test the C extension has the same interface as the Python version."""

    def test_c_extension_available(self):
        """Check if C extension is importable (may not be compiled)."""
        try:
            from rxdjango.utils import delta_utils_c
            assert hasattr(delta_utils_c, 'generate_delta')
        except ImportError:
            pytest.skip("C extension not compiled")

    def test_c_extension_matches_python(self):
        """C extension should produce same results as Python version."""
        try:
            from rxdjango.utils import delta_utils_c
        except ImportError:
            pytest.skip("C extension not compiled")

        original = {'id': 1, 'name': 'foo', 'status': 'active'}

        # Test with Python version
        py_instance = {'id': 1, 'name': 'bar', 'status': 'active'}
        py_result = generate_delta(original.copy(), py_instance)

        # Test with C version
        c_instance = {'id': 1, 'name': 'bar', 'status': 'active'}
        c_result = delta_utils_c.generate_delta(original.copy(), c_instance)

        assert len(py_result) == len(c_result)
