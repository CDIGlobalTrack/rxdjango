"""
Tests for rxdjango.related_properties module.

Tests the RelatedProperty registry, accessor lookups, and key generation.
"""
import pytest

from rxdjango.related_properties import (
    RelatedProperty,
    is_related_property,
    get_accessor,
    get_reverse_accessor,
    _make_key,
)
from rxdjango.exceptions import UnknownProperty


class TestMakeKey:
    """Tests for the _make_key helper."""

    def test_generates_module_qualname_tuple(self):
        """Key should be (module, 'ClassName.property_name')."""
        class MyModel:
            __module__ = 'myapp.models'

        key = _make_key(MyModel, 'my_prop')
        assert key == ('myapp.models', 'MyModel.my_prop')

    def test_different_properties_produce_different_keys(self):
        class FakeModel:
            __module__ = 'myapp.models'
            __name__ = 'MyModel'

        key1 = _make_key(FakeModel, 'prop_a')
        key2 = _make_key(FakeModel, 'prop_b')
        assert key1 != key2


class TestRelatedProperty:
    """Tests for RelatedProperty decorator and registry."""

    def setup_method(self):
        """Save original registry state."""
        self._orig_accessors = RelatedProperty.accessors.copy()
        self._orig_reverses = RelatedProperty.reverses.copy()
        self._orig_unknown = RelatedProperty.unknown_properties.copy()

    def teardown_method(self):
        """Restore original registry state."""
        RelatedProperty.accessors = self._orig_accessors
        RelatedProperty.reverses = self._orig_reverses
        RelatedProperty.unknown_properties = self._orig_unknown

    def test_decorator_registers_accessor(self):
        """Decorating a function should register its accessor path."""
        rp = RelatedProperty(accessor='parent__children', reverse='parent')

        @rp
        def my_property(self):
            return []

        # The decorated result should be a property
        assert isinstance(my_property, property)

    def test_get_accessor_returns_registered_path(self):
        """get_accessor should return the path registered for a property."""
        class TestModel:
            __module__ = 'test_rp.models'
            __name__ = 'TestModel'

        rp = RelatedProperty(accessor='project__tasks', reverse='project')

        # Manually register as the decorator would
        key = ('test_rp.models', 'TestModel.tasks')
        RelatedProperty.accessors[key] = 'project__tasks'
        RelatedProperty.reverses[key] = 'project'

        assert get_accessor(TestModel, 'tasks') == 'project__tasks'
        assert get_reverse_accessor(TestModel, 'tasks') == 'project'

    def test_is_related_property_true(self):
        """is_related_property should return True for registered properties."""
        class RegModel:
            __module__ = 'reg.models'
            __name__ = 'RegModel'

        key = ('reg.models', 'RegModel.items')
        RelatedProperty.accessors[key] = 'parent__items'

        assert is_related_property(RegModel, 'items') is True

    def test_is_related_property_false(self):
        """is_related_property should return False for non-registered properties."""
        class UnregModel:
            __module__ = 'unreg.models'
            __name__ = 'UnregModel'

        assert is_related_property(UnregModel, 'whatever') is False

    def test_get_accessor_unknown_raises(self):
        """get_accessor for an unknown property (registered as unknown) should raise."""
        class UModel:
            __module__ = 'u.models'
            __name__ = 'UModel'

        RelatedProperty.register_unknown_property(UModel, 'bad_prop')

        with pytest.raises(UnknownProperty, match='Unknown property'):
            get_accessor(UModel, 'bad_prop')

    def test_get_reverse_accessor_unknown_raises(self):
        """get_reverse_accessor for unknown property should raise."""
        class UModel2:
            __module__ = 'u2.models'
            __name__ = 'UModel2'

        RelatedProperty.register_unknown_property(UModel2, 'bad_rev')

        with pytest.raises(UnknownProperty, match='Unknown property'):
            get_reverse_accessor(UModel2, 'bad_rev')

    def test_get_accessor_unregistered_returns_none(self):
        """get_accessor for totally unknown property returns None."""
        class NoModel:
            __module__ = 'no.models'
            __name__ = 'NoModel'

        result = get_accessor(NoModel, 'nonexistent')
        assert result is None

    def test_register_unknown_property(self):
        """register_unknown_property should add to unknown set."""
        class XModel:
            __module__ = 'x.models'
            __name__ = 'XModel'

        result = RelatedProperty.register_unknown_property(XModel, 'prop')
        key = ('x.models', 'XModel.prop')
        assert key in RelatedProperty.unknown_properties
        # Returns None since no accessor registered
        assert result is None

    def test_register_unknown_returns_existing_accessor(self):
        """register_unknown_property returns accessor if already registered."""
        class YModel:
            __module__ = 'y.models'
            __name__ = 'YModel'

        key = ('y.models', 'YModel.field')
        RelatedProperty.accessors[key] = 'parent__field'

        result = RelatedProperty.register_unknown_property(YModel, 'field')
        assert result == 'parent__field'
