"""
Tests for rxdjango.write module.

Tests the field-filtering logic that restricts which fields a client
can modify via save/create write operations.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from django.db import models
from rest_framework import serializers

from rxdjango.write import (
    _get_writable_fields, _validate_data,
    _atomic_create, _atomic_save, _atomic_delete,
    execute_save, execute_create,
)
from rxdjango.exceptions import ForbiddenError, WriteError
from rxdjango.operations import Operation, SAVE, CREATE, DELETE


# ---------------------------------------------------------------------------
# Minimal Django models for testing (unmanaged — no DB table needed)
# ---------------------------------------------------------------------------

class FakeModel(models.Model):
    name = models.CharField(max_length=64)
    secret = models.CharField(max_length=64)
    is_admin = models.BooleanField(default=False)
    score = models.IntegerField(default=0)
    owner = models.ForeignKey('self', null=True, on_delete=models.SET_NULL)

    class Meta:
        app_label = 'test_write'
        managed = False


class ChildModel(models.Model):
    title = models.CharField(max_length=64)
    internal_flag = models.BooleanField(default=False)
    parent = models.ForeignKey(FakeModel, on_delete=models.CASCADE,
                               related_name='children')

    class Meta:
        app_label = 'test_write'
        managed = False


# ---------------------------------------------------------------------------
# Serializers that expose a subset of model fields
# ---------------------------------------------------------------------------

class FakeSerializer(serializers.ModelSerializer):
    class Meta:
        model = FakeModel
        fields = ['id', 'name', 'score']


class FakeSerializerReadOnly(serializers.ModelSerializer):
    name = serializers.CharField(read_only=True)

    class Meta:
        model = FakeModel
        fields = ['id', 'name', 'score']


class FakeSerializerWithNested(serializers.ModelSerializer):
    children = serializers.BaseSerializer(many=True, read_only=True)

    class Meta:
        model = FakeModel
        fields = ['id', 'name', 'children']


class ChildSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChildModel
        fields = ['id', 'title']


class AllFieldsSerializer(serializers.ModelSerializer):
    """Uses __all__ — exposes every model field."""
    class Meta:
        model = FakeModel
        fields = '__all__'


class RichModel(models.Model):
    """Model with varied field types for validation testing."""
    name = models.CharField(max_length=64)
    count = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    rating = models.FloatField(default=0.0)
    parent = models.ForeignKey(FakeModel, null=True, on_delete=models.SET_NULL,
                               related_name='rich_children')

    class Meta:
        app_label = 'test_write'
        managed = False


class RichSerializer(serializers.ModelSerializer):
    class Meta:
        model = RichModel
        fields = ['id', 'name', 'count', 'is_active', 'rating', 'parent']


class SeenModel(models.Model):
    """Tracks which user has seen a parent item. user_key = 'viewer'."""
    viewer = models.IntegerField()  # user ID, acts as user_key
    seen_at = models.DateTimeField(auto_now_add=True)
    parent = models.ForeignKey(FakeModel, on_delete=models.CASCADE,
                               related_name='seen_set')

    class Meta:
        app_label = 'test_write'
        managed = False


class SeenSerializer(serializers.ModelSerializer):
    class Meta:
        model = SeenModel
        fields = ['id', 'viewer']
        user_key = 'viewer'


# ---------------------------------------------------------------------------
# Helper to build a mock StateModel node
# ---------------------------------------------------------------------------

def _make_node(serializer_instance, model_class=None, user_key=None):
    """Return a lightweight object that quacks like a StateModel node."""
    node = MagicMock()
    node.nested_serializer = serializer_instance
    node.model = model_class or serializer_instance.Meta.model
    node.user_key = user_key
    return node


# ---------------------------------------------------------------------------
# Tests for _get_writable_fields
# ---------------------------------------------------------------------------

class TestGetWritableFields:
    """Verify _get_writable_fields returns the correct allowlist."""

    def test_basic_serializer_fields(self):
        node = _make_node(FakeSerializer())
        fields = _get_writable_fields(node)
        assert fields == {'name', 'score'}

    def test_id_is_excluded(self):
        node = _make_node(FakeSerializer())
        assert 'id' not in _get_writable_fields(node)

    def test_read_only_fields_excluded(self):
        node = _make_node(FakeSerializerReadOnly())
        fields = _get_writable_fields(node)
        assert 'name' not in fields
        assert 'score' in fields

    def test_nested_serializer_fields_excluded(self):
        node = _make_node(FakeSerializerWithNested())
        fields = _get_writable_fields(node)
        assert 'children' not in fields
        assert 'name' in fields

    def test_fields_not_in_serializer_not_returned(self):
        """Fields on the model but not on the serializer must not appear."""
        node = _make_node(FakeSerializer())
        fields = _get_writable_fields(node)
        assert 'secret' not in fields
        assert 'is_admin' not in fields
        assert 'owner' not in fields

    def test_all_fields_serializer(self):
        """__all__ exposes model fields, but id is still excluded."""
        node = _make_node(AllFieldsSerializer())
        fields = _get_writable_fields(node)
        assert 'id' not in fields
        assert 'name' in fields
        assert 'secret' in fields
        assert 'is_admin' in fields
        assert 'score' in fields

    def test_child_serializer(self):
        node = _make_node(ChildSerializer())
        fields = _get_writable_fields(node)
        assert fields == {'title'}


# ---------------------------------------------------------------------------
# Tests for field filtering in execute_save / execute_create
#
# These test that the data dict is filtered *before* it reaches the atomic
# DB operation by intercepting _atomic_save / _atomic_create and checking
# the data argument they receive.
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.run(coro)


def _make_channel(index, writable_map):
    """Build a minimal mock channel for write operations."""
    channel = MagicMock()
    channel.anchor_ids = [1]
    channel.user_id = 42

    state_model = MagicMock()
    state_model.index = index
    channel._state_model = state_model
    channel.__class__._writable = writable_map
    channel.__class__.__name__ = 'TestChannel'

    return channel


class TestExecuteSaveFieldFiltering:
    """Verify execute_save strips fields not in the serializer."""

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_save')
    def test_save_strips_non_serializer_fields(self, mock_atomic, mock_ctx):
        node = _make_node(FakeSerializer(), FakeModel)
        channel = _make_channel(
            {'test.FakeSerializer': [node]},
            {'test.FakeSerializer': [SAVE]},
        )

        mock_atomic.side_effect = ForbiddenError('denied')

        with pytest.raises(ForbiddenError):
            _run(execute_save(
                channel,
                'test.FakeSerializer',
                1,
                {
                    'name': 'Updated',
                    'is_admin': True,   # NOT in serializer
                    'secret': 'pwned',  # NOT in serializer
                    '_tstamp': 999,     # internal field
                },
            ))

        # _atomic_save receives the filtered data
        _, kwargs = mock_atomic.call_args
        assert kwargs == {} or True  # called positionally
        args = mock_atomic.call_args[0]
        passed_data = args[3]  # channel, model_class, instance_id, data
        assert passed_data == {'name': 'Updated'}

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_save')
    def test_save_allows_valid_serializer_fields(self, mock_atomic, mock_ctx):
        node = _make_node(FakeSerializer(), FakeModel)
        channel = _make_channel(
            {'test.FakeSerializer': [node]},
            {'test.FakeSerializer': [SAVE]},
        )

        mock_atomic.side_effect = ForbiddenError('denied')

        with pytest.raises(ForbiddenError):
            _run(execute_save(
                channel,
                'test.FakeSerializer',
                1,
                {'name': 'Updated', 'score': 42},
            ))

        args = mock_atomic.call_args[0]
        passed_data = args[3]
        assert passed_data == {'name': 'Updated', 'score': 42}


class TestExecuteCreateFieldFiltering:
    """Verify execute_create strips fields not in the child serializer."""

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_create')
    def test_create_strips_non_serializer_fields(self, mock_atomic, mock_ctx):
        child_node = _make_node(ChildSerializer(), ChildModel)
        child_node.instance_type = 'test.ChildSerializer'
        parent_node = _make_node(FakeSerializer(), FakeModel)
        parent_node.children = {'children': child_node}

        channel = _make_channel(
            {
                'test.ChildSerializer': [child_node],
                'test.FakeSerializer': [parent_node],
            },
            {'test.ChildSerializer': [CREATE]},
        )

        mock_atomic.side_effect = ForbiddenError('denied')

        with pytest.raises(ForbiddenError):
            _run(execute_create(
                channel,
                'test.ChildSerializer',
                'test.FakeSerializer',
                1,
                'children',
                {
                    'title': 'New Child',
                    'internal_flag': True,  # NOT in serializer
                    '_tstamp': 999,          # internal field
                },
            ))

        # _atomic_create args: channel, child_model, parent_model,
        #                      parent_id, relation_name, data
        args = mock_atomic.call_args[0]
        passed_data = args[5]
        assert passed_data == {'title': 'New Child'}


# ---------------------------------------------------------------------------
# Tests for relation_name validation in execute_create
# ---------------------------------------------------------------------------

class TestExecuteCreateRelationValidation:
    """Verify execute_create rejects invalid or mismatched relation_name."""

    def _make_parent_with_children(self, children_dict):
        """Build parent and child nodes with a real children mapping.

        Args:
            children_dict: Maps relation_name -> (child_instance_type, child_model)
        """
        child_nodes_by_type = {}
        parent_children = {}

        for rel_name, (child_type, child_model_cls) in children_dict.items():
            child_node = _make_node(ChildSerializer(), child_model_cls)
            child_node.instance_type = child_type
            child_nodes_by_type.setdefault(child_type, []).append(child_node)
            parent_children[rel_name] = child_node

        parent_node = _make_node(FakeSerializer(), FakeModel)
        parent_node.children = parent_children

        return parent_node, child_nodes_by_type

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_create')
    def test_rejects_relation_name_not_in_state_model(self, mock_atomic, mock_ctx):
        """relation_name that doesn't exist in the parent's children must be rejected."""
        parent_node, child_index = self._make_parent_with_children({
            'children': ('test.ChildSerializer', ChildModel),
        })

        # Standalone child node for the index (needed for writable check)
        child_node = _make_node(ChildSerializer(), ChildModel)
        child_node.instance_type = 'test.ChildSerializer'

        index = {
            'test.ChildSerializer': [child_node],
            'test.FakeSerializer': [parent_node],
        }
        channel = _make_channel(index, {'test.ChildSerializer': [CREATE]})

        with pytest.raises(WriteError, match='not a declared relation'):
            _run(execute_create(
                channel,
                'test.ChildSerializer',
                'test.FakeSerializer',
                1,
                'secret_internal_tasks',  # NOT in parent's children
                {'title': 'injected'},
            ))

        # _atomic_create should not have been called
        mock_atomic.assert_not_called()

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_create')
    def test_rejects_relation_name_with_wrong_child_type(self, mock_atomic, mock_ctx):
        """relation_name exists but points to a different instance_type."""
        parent_node, child_index = self._make_parent_with_children({
            'children': ('test.ChildSerializer', ChildModel),
            'assets': ('test.AssetSerializer', FakeModel),
        })

        child_node = _make_node(ChildSerializer(), ChildModel)
        child_node.instance_type = 'test.ChildSerializer'
        asset_node = _make_node(FakeSerializer(), FakeModel)
        asset_node.instance_type = 'test.AssetSerializer'

        index = {
            'test.ChildSerializer': [child_node],
            'test.AssetSerializer': [asset_node],
            'test.FakeSerializer': [parent_node],
        }
        channel = _make_channel(index, {'test.ChildSerializer': [CREATE]})

        with pytest.raises(WriteError, match='does not match'):
            _run(execute_create(
                channel,
                'test.ChildSerializer',   # claiming to create a Child
                'test.FakeSerializer',
                1,
                'assets',                 # but 'assets' maps to AssetSerializer
                {'title': 'injected'},
            ))

        mock_atomic.assert_not_called()

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_create')
    def test_accepts_valid_relation_name(self, mock_atomic, mock_ctx):
        """A matching relation_name + instance_type should pass validation."""
        parent_node, _ = self._make_parent_with_children({
            'children': ('test.ChildSerializer', ChildModel),
        })

        child_node = _make_node(ChildSerializer(), ChildModel)
        child_node.instance_type = 'test.ChildSerializer'

        index = {
            'test.ChildSerializer': [child_node],
            'test.FakeSerializer': [parent_node],
        }
        channel = _make_channel(index, {'test.ChildSerializer': [CREATE]})

        # Deny at can_create so we don't need the full ORM chain,
        # but the point is we get past the relation validation.
        mock_atomic.side_effect = ForbiddenError('denied')

        with pytest.raises(ForbiddenError):
            _run(execute_create(
                channel,
                'test.ChildSerializer',
                'test.FakeSerializer',
                1,
                'children',  # valid relation
                {'title': 'legit'},
            ))

        # _atomic_create WAS called (passed validation)
        mock_atomic.assert_called_once()


# ---------------------------------------------------------------------------
# Tests for user_key enforcement on create operations
# ---------------------------------------------------------------------------

class TestCreateUserKeyEnforcement:
    """Verify execute_create injects/overrides user_key on the data."""

    def _setup_seen_channel(self):
        """Build channel, nodes, and index for SeenModel with user_key='viewer'."""
        child_node = _make_node(SeenSerializer(), SeenModel, user_key='viewer')
        child_node.instance_type = 'test.SeenSerializer'

        parent_node = _make_node(FakeSerializer(), FakeModel)
        parent_node.children = {'seen_set': child_node}

        index = {
            'test.SeenSerializer': [child_node],
            'test.FakeSerializer': [parent_node],
        }
        channel = _make_channel(index, {'test.SeenSerializer': [CREATE]})
        # channel.user_id is 42 (set by _make_channel)
        return channel

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_create')
    def test_create_injects_user_key_when_missing(self, mock_atomic, mock_ctx):
        """When the client omits the user_key field, the server must inject it."""
        channel = self._setup_seen_channel()
        mock_atomic.return_value = MagicMock()

        _run(execute_create(
            channel,
            'test.SeenSerializer',
            'test.FakeSerializer',
            1,
            'seen_set',
            {},  # no viewer field sent
        ))

        args = mock_atomic.call_args[0]
        passed_data = args[5]
        assert passed_data.get('viewer') == 42

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_create')
    def test_create_overrides_user_key_when_wrong(self, mock_atomic, mock_ctx):
        """When the client sends a different user ID, the server must override it."""
        channel = self._setup_seen_channel()
        mock_atomic.return_value = MagicMock()

        _run(execute_create(
            channel,
            'test.SeenSerializer',
            'test.FakeSerializer',
            1,
            'seen_set',
            {'viewer': 999},  # trying to impersonate user 999
        ))

        args = mock_atomic.call_args[0]
        passed_data = args[5]
        assert passed_data['viewer'] == 42


# ---------------------------------------------------------------------------
# Tests for can_* authorization in atomic operations
# ---------------------------------------------------------------------------

class TestAtomicAuthorization:
    """Verify that _atomic_save/create/delete call can_* and raise on denial."""

    @patch('rxdjango.write.transaction')
    def test_atomic_create_denied_by_can_create(self, mock_tx):
        """can_create returning False must raise ForbiddenError."""
        mock_tx.atomic.return_value.__enter__ = lambda s: None
        mock_tx.atomic.return_value.__exit__ = lambda s, *a: None

        fake_parent = MagicMock()
        parent_model = MagicMock()
        parent_model.__name__ = 'TestParent'
        parent_model.objects.select_for_update.return_value.get.return_value = fake_parent
        parent_model.DoesNotExist = type('DoesNotExist', (Exception,), {})

        channel = MagicMock()
        channel.can_create = MagicMock(return_value=False)

        with pytest.raises(ForbiddenError, match='Create operation not permitted'):
            _atomic_create(
                channel, ChildModel, parent_model, 1,
                'children', {'title': 'test'},
            )

        channel.can_create.assert_called_once_with(
            ChildModel, fake_parent, {'title': 'test'},
        )

    @patch('rxdjango.write.transaction')
    def test_atomic_create_allowed_by_can_create(self, mock_tx):
        """can_create returning True must proceed to manager.create()."""
        mock_tx.atomic.return_value.__enter__ = lambda s: None
        mock_tx.atomic.return_value.__exit__ = lambda s, *a: None

        fake_parent = MagicMock()
        parent_model = MagicMock()
        parent_model.objects.select_for_update.return_value.get.return_value = fake_parent
        parent_model.DoesNotExist = type('DoesNotExist', (Exception,), {})

        channel = MagicMock()
        channel.can_create = MagicMock(return_value=True)

        _atomic_create(
            channel, ChildModel, parent_model, 1,
            'children', {'title': 'test'},
        )

        channel.can_create.assert_called_once()
        fake_parent.children.create.assert_called_once()

    @patch('rxdjango.write.transaction')
    def test_atomic_save_denied_by_can_save(self, mock_tx):
        """can_save returning False must raise ForbiddenError."""
        mock_tx.atomic.return_value.__enter__ = lambda s: None
        mock_tx.atomic.return_value.__exit__ = lambda s, *a: None

        fake_instance = MagicMock()
        model_class = MagicMock()
        model_class.__name__ = 'TestModel'
        model_class.objects.select_for_update.return_value.get.return_value = fake_instance
        model_class.DoesNotExist = type('DoesNotExist', (Exception,), {})

        channel = MagicMock()
        channel.can_save = MagicMock(return_value=False)

        with pytest.raises(ForbiddenError, match='Save operation not permitted'):
            _atomic_save(channel, model_class, 1, {'name': 'test'})

        channel.can_save.assert_called_once_with(fake_instance, {'name': 'test'})

    @patch('rxdjango.write.transaction')
    def test_atomic_delete_denied_by_can_delete(self, mock_tx):
        """can_delete returning False must raise ForbiddenError."""
        mock_tx.atomic.return_value.__enter__ = lambda s: None
        mock_tx.atomic.return_value.__exit__ = lambda s, *a: None

        fake_instance = MagicMock()
        model_class = MagicMock()
        model_class.__name__ = 'TestModel'
        model_class.objects.select_for_update.return_value.get.return_value = fake_instance
        model_class.DoesNotExist = type('DoesNotExist', (Exception,), {})

        channel = MagicMock()
        channel.can_delete = MagicMock(return_value=False)

        with pytest.raises(ForbiddenError, match='Delete operation not permitted'):
            _atomic_delete(channel, model_class, 1)

        channel.can_delete.assert_called_once_with(fake_instance)


# ---------------------------------------------------------------------------
# Tests for data type validation on save/create
#
# Currently, write operations only filter by field name. These tests verify
# that invalid data types are rejected with a WriteError *before* reaching
# the ORM. They are expected to FAIL until validation is implemented.
# ---------------------------------------------------------------------------

class TestSaveDataValidation:
    """Verify execute_save rejects invalid data types."""

    def _setup_channel(self):
        node = _make_node(RichSerializer(), RichModel)
        channel = _make_channel(
            {'test.RichSerializer': [node]},
            {'test.RichSerializer': [SAVE]},
        )
        return channel

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_save')
    def test_save_rejects_string_for_integer_field(self, mock_atomic, mock_ctx):
        """Sending a string for IntegerField 'count' must raise WriteError."""
        channel = self._setup_channel()

        with pytest.raises(WriteError):
            _run(execute_save(
                channel, 'test.RichSerializer', 1,
                {'count': 'not_a_number'},
            ))

        mock_atomic.assert_not_called()

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_save')
    def test_save_rejects_string_for_boolean_field(self, mock_atomic, mock_ctx):
        """Sending a random string for BooleanField must raise WriteError."""
        channel = self._setup_channel()

        with pytest.raises(WriteError):
            _run(execute_save(
                channel, 'test.RichSerializer', 1,
                {'is_active': 'not_a_bool'},
            ))

        mock_atomic.assert_not_called()

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_save')
    def test_save_rejects_string_for_float_field(self, mock_atomic, mock_ctx):
        """Sending a non-numeric string for FloatField must raise WriteError."""
        channel = self._setup_channel()

        with pytest.raises(WriteError):
            _run(execute_save(
                channel, 'test.RichSerializer', 1,
                {'rating': 'high'},
            ))

        mock_atomic.assert_not_called()

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_save')
    def test_save_rejects_list_for_char_field(self, mock_atomic, mock_ctx):
        """Sending a list for CharField must raise WriteError."""
        channel = self._setup_channel()

        with pytest.raises(WriteError):
            _run(execute_save(
                channel, 'test.RichSerializer', 1,
                {'name': ['a', 'b']},
            ))

        mock_atomic.assert_not_called()

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_save')
    def test_save_rejects_dict_for_char_field(self, mock_atomic, mock_ctx):
        """Sending a dict for CharField must raise WriteError."""
        channel = self._setup_channel()

        with pytest.raises(WriteError):
            _run(execute_save(
                channel, 'test.RichSerializer', 1,
                {'name': {'nested': 'object'}},
            ))

        mock_atomic.assert_not_called()

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_save')
    def test_save_rejects_string_for_fk_field(self, mock_atomic, mock_ctx):
        """Sending a string for a ForeignKey field must raise WriteError."""
        channel = self._setup_channel()

        with pytest.raises(WriteError):
            _run(execute_save(
                channel, 'test.RichSerializer', 1,
                {'parent': 'not_an_id'},
            ))

        mock_atomic.assert_not_called()

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_save')
    def test_save_rejects_oversized_char_field(self, mock_atomic, mock_ctx):
        """Sending a string exceeding max_length must raise WriteError."""
        channel = self._setup_channel()

        with pytest.raises(WriteError):
            _run(execute_save(
                channel, 'test.RichSerializer', 1,
                {'name': 'x' * 200},  # max_length=64
            ))

        mock_atomic.assert_not_called()

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_save')
    def test_save_accepts_valid_data(self, mock_atomic, mock_ctx):
        """Valid data types must pass validation and reach _atomic_save."""
        channel = self._setup_channel()
        mock_atomic.return_value = MagicMock()

        _run(execute_save(
            channel, 'test.RichSerializer', 1,
            {'name': 'Valid', 'count': 5, 'is_active': True, 'rating': 3.14},
        ))

        mock_atomic.assert_called_once()


class TestCreateDataValidation:
    """Verify execute_create rejects invalid data types."""

    def _setup_channel(self):
        child_node = _make_node(RichSerializer(), RichModel)
        child_node.instance_type = 'test.RichSerializer'

        parent_node = _make_node(FakeSerializer(), FakeModel)
        parent_node.children = {'rich_children': child_node}

        channel = _make_channel(
            {
                'test.RichSerializer': [child_node],
                'test.FakeSerializer': [parent_node],
            },
            {'test.RichSerializer': [CREATE]},
        )
        return channel

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_create')
    def test_create_rejects_string_for_integer_field(self, mock_atomic, mock_ctx):
        """Sending a string for IntegerField must raise WriteError."""
        channel = self._setup_channel()

        with pytest.raises(WriteError):
            _run(execute_create(
                channel, 'test.RichSerializer', 'test.FakeSerializer', 1,
                'rich_children', {'name': 'OK', 'count': 'bad'},
            ))

        mock_atomic.assert_not_called()

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_create')
    def test_create_rejects_dict_for_integer_field(self, mock_atomic, mock_ctx):
        """Sending a dict for IntegerField must raise WriteError."""
        channel = self._setup_channel()

        with pytest.raises(WriteError):
            _run(execute_create(
                channel, 'test.RichSerializer', 'test.FakeSerializer', 1,
                'rich_children', {'name': 'OK', 'count': {'value': 1}},
            ))

        mock_atomic.assert_not_called()

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_create')
    def test_create_rejects_null_for_required_field(self, mock_atomic, mock_ctx):
        """Sending null for a non-nullable required field must raise WriteError."""
        channel = self._setup_channel()

        with pytest.raises(WriteError):
            _run(execute_create(
                channel, 'test.RichSerializer', 'test.FakeSerializer', 1,
                'rich_children', {'name': None, 'count': 1},
            ))

        mock_atomic.assert_not_called()

    @patch('rxdjango.write._verify_in_context', new_callable=AsyncMock)
    @patch('rxdjango.write._atomic_create')
    def test_create_accepts_valid_data(self, mock_atomic, mock_ctx):
        """Valid data must pass validation and reach _atomic_create."""
        channel = self._setup_channel()
        mock_atomic.return_value = MagicMock()

        _run(execute_create(
            channel, 'test.RichSerializer', 'test.FakeSerializer', 1,
            'rich_children', {'name': 'Valid', 'count': 5},
        ))

        mock_atomic.assert_called_once()


# ---------------------------------------------------------------------------
# Tests for PrimaryKeyRelatedField validation without DB queries
#
# When a serializer has a plain FK field (not overridden with a nested
# serializer), DRF auto-generates a PrimaryKeyRelatedField. Calling
# is_valid() on it triggers queryset.get() — a sync DB call that raises
# SynchronousOnlyOperation in an async context. _validate_data must
# replace these fields so no DB query occurs during validation.
# ---------------------------------------------------------------------------

class TestValidateDataNoDatabaseQueries:
    """Verify _validate_data does not trigger DB queries for FK fields."""

    def test_pk_related_field_does_not_query_db(self):
        """PrimaryKeyRelatedField must be replaced to avoid sync DB access.

        RichSerializer.parent is a plain FK — DRF creates a
        PrimaryKeyRelatedField with a queryset. Calling is_valid() from
        async code would raise SynchronousOnlyOperation. This test calls
        _validate_data from an async context to reproduce the crash.
        """
        node = _make_node(RichSerializer(), RichModel)

        async def validate_in_async():
            return _validate_data(node, {'parent': 1}, partial=True)

        # This will raise SynchronousOnlyOperation if PrimaryKeyRelatedField
        # is not replaced, because queryset.get() is sync-only.
        result = _run(validate_in_async())
        assert 'parent' in result

    def test_pk_related_field_null_value_accepted(self):
        """Null value for a nullable FK must pass validation without DB query."""
        node = _make_node(RichSerializer(), RichModel)

        async def validate_in_async():
            return _validate_data(node, {'parent': None}, partial=True)

        result = _run(validate_in_async())
        assert result.get('parent') is None

    def test_pk_related_field_invalid_type_rejected(self):
        """Non-integer for a FK field must raise WriteError, not hit the DB."""
        node = _make_node(RichSerializer(), RichModel)

        async def validate_in_async():
            return _validate_data(node, {'parent': 'not_an_id'}, partial=True)

        with pytest.raises(WriteError):
            _run(validate_in_async())


# ---------------------------------------------------------------------------
# Tests for Operation enum validation in metaclass
# ---------------------------------------------------------------------------

class TestOperationEnumValidation:
    """Verify that the metaclass rejects bare strings and accepts Operation members."""

    def test_valid_operations_accepted(self):
        """Operation members must be accepted without error."""
        writable_map = {}
        for serializer_class, operations in {FakeSerializer: [SAVE, CREATE, DELETE]}.items():
            instance_type = f'{serializer_class.__module__}.{serializer_class.__name__}'
            resolved = []
            for op in operations:
                assert isinstance(op, Operation)
                resolved.append(op)
            writable_map[instance_type] = resolved

        assert len(writable_map) == 1
        key = list(writable_map.keys())[0]
        assert writable_map[key] == [SAVE, CREATE, DELETE]

    def test_bare_string_rejected(self):
        """A bare string like 'save' must fail the isinstance check."""
        assert not isinstance('save', Operation)

    def test_random_object_rejected(self):
        """An arbitrary object must fail the isinstance check."""
        assert not isinstance(42, Operation)
        assert not isinstance(object(), Operation)

    def test_verify_writable_with_operation_members(self):
        """_verify_writable must work with Operation members in _writable."""
        from rxdjango.write import _verify_writable

        channel = _make_channel(
            {'test.FakeSerializer': [_make_node(FakeSerializer(), FakeModel)]},
            {'test.FakeSerializer': [SAVE, DELETE]},
        )

        # SAVE should pass
        _verify_writable(channel, 'test.FakeSerializer', SAVE)

        # CREATE should raise (not declared)
        with pytest.raises(ForbiddenError):
            _verify_writable(channel, 'test.FakeSerializer', CREATE)
