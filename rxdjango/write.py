"""Write operations for optimistic updates.

This module handles write operations (save, create, delete) from the frontend,
performing authorization checks before executing database operations.

Each operation verifies that the target instance belongs to the channel's
current anchor context by checking the MongoDB cache before proceeding.

All database operations (load, authorize, mutate) are wrapped in
``transaction.atomic()`` with ``select_for_update()`` to prevent TOCTOU
race conditions between the authorization check and the actual write.
"""
from __future__ import annotations

import logging
from typing import Any
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import models, transaction
from django.db.models import Model
from rest_framework import serializers

from .exceptions import ForbiddenError, WriteError
from .mongo import get_motor_client
from .operations import Operation, SAVE, CREATE, DELETE

logger = logging.getLogger('rxdjango.write')


def _validate_data(model_node, data: dict[str, Any], partial: bool = False) -> dict[str, Any]:
    """Validate client data using the DRF serializer.

    Instantiates the node's serializer class with the client data and runs
    ``is_valid()``.  Single nested serializers (representing ForeignKey
    fields where the client sends an integer PK) are temporarily replaced
    with ``IntegerField`` so the serializer accepts the raw ID value.

    Returns ``validated_data`` (with coerced types) on success, or raises
    ``WriteError`` on validation failure.

    Args:
        model_node: A StateModel node whose ``nested_serializer`` provides
            the serializer class.
        data: The client-supplied field dict to validate.
        partial: If True, missing fields are acceptable (used for save).

    Returns:
        The ``validated_data`` dict from the serializer.

    Raises:
        WriteError: If the data fails serializer validation.
    """
    serializer_class = model_node.nested_serializer.__class__
    serializer = serializer_class(data=data, partial=partial)

    # Replace FK-related fields with IntegerField to avoid DB queries
    # during validation.  This covers two cases:
    #   1. Single nested serializers (e.g. developer = ParticipantSerializer())
    #      where the client sends an integer PK, not a nested object.
    #   2. PrimaryKeyRelatedField (auto-generated for plain FK fields) whose
    #      is_valid() calls queryset.get() — a sync DB query that raises
    #      SynchronousOnlyOperation in async contexts.
    for name, field in list(serializer.fields.items()):
        is_nested_fk = (isinstance(field, serializers.BaseSerializer) and
                        not getattr(field, 'many', False))
        is_pk_related = isinstance(field, serializers.RelatedField)
        if (is_nested_fk or is_pk_related) and name in data:
            allow_null = getattr(field, 'allow_null', False) or field.required is False
            serializer.fields[name] = serializers.IntegerField(
                allow_null=allow_null, required=field.required,
            )

    if not serializer.is_valid():
        raise WriteError(f'Invalid data: {serializer.errors}')
    return serializer.validated_data


def _get_writable_fields(model_node) -> set[str]:
    """Return the set of field names that the serializer exposes as writable.

    Only fields declared on the nested serializer that are not read-only
    and not nested serializers (relations managed by the state tree) are
    included.  The ``id`` field is always excluded — primary keys are
    never client-writable.

    Args:
        model_node: A StateModel node whose ``nested_serializer`` will be
            inspected.

    Returns:
        A set of field name strings the client is allowed to send in
        ``data`` for save / create operations.
    """
    serializer = model_node.nested_serializer
    allowed = set()
    for name, field in serializer.fields.items():
        if name == 'id':
            continue
        if getattr(field, 'read_only', False):
            continue
        # Skip many=True nested serializers — those are child collections
        # managed by the state tree, not directly writable fields.
        # Single nested serializers (many=False) represent ForeignKeys
        # where the client sends the FK ID value.
        if isinstance(field, serializers.BaseSerializer) and getattr(field, 'many', False):
            continue
        allowed.add(name)
    return allowed


def _verify_writable(channel, instance_type: str, operation: Operation) -> None:
    """Verify that the operation is declared in Meta.writable for this type.

    Args:
        channel: The ContextChannel instance (has __class__._writable)
        instance_type: The _instance_type string of the serializer
        operation: One of 'save', 'create', 'delete'

    Raises:
        ForbiddenError: If the operation is not declared for this type
    """
    writable = getattr(channel.__class__, '_writable', {})
    allowed_ops = writable.get(instance_type, [])
    if operation not in allowed_ops:
        logger.warning(
            'write denied: %s on %s by user %s (not declared in Meta.writable) [%s]',
            operation, instance_type, channel.user_id, channel.__class__.__name__,
        )
        raise ForbiddenError(
            f'{operation} not permitted on {instance_type}'
        )


async def _verify_in_context(channel, instance_type: str, instance_id: int) -> None:
    """Verify that an instance belongs to the channel's current anchor context.

    Checks the MongoDB cache for a document matching the instance under one
    of the channel's anchor IDs. If not found, raises WriteError.

    Args:
        channel: The ContextChannel instance (has anchor_ids and __class__)
        instance_type: The _instance_type string of the serializer
        instance_id: The ID of the instance to verify

    Raises:
        WriteError: If the instance is not in the channel's context
    """
    client = get_motor_client()
    db = client[settings.MONGO_STATE_DB]
    collection = db[channel.__class__.__name__.lower()]

    doc = await collection.find_one({
        '_anchor_id': {'$in': channel.anchor_ids},
        '_instance_type': instance_type,
        'id': instance_id,
        '_user_key': {'$in': [None, channel.user_id]},
    })

    if doc is None:
        logger.warning(
            'write denied: %s:%s not in context for user %s [%s]',
            instance_type, instance_id, channel.user_id, channel.__class__.__name__,
        )
        raise WriteError(
            f'Instance {instance_type}:{instance_id} is not in this context'
        )


def _resolve_fk_values(model_class, data: dict[str, Any]) -> dict[str, Any]:
    """Resolve ForeignKey fields in data from IDs to model instances.

    Args:
        model_class: The Django model class to inspect for FK fields.
        data: Field name → value dict. FK fields should contain the PK
            of the related instance.

    Returns:
        A new dict with FK values replaced by loaded model instances.

    Raises:
        WriteError: If a related instance does not exist.
    """
    resolved = {}
    for field_name, value in data.items():
        field = model_class._meta.get_field(field_name)
        if isinstance(field, models.ForeignKey):
            related_model = field.related_model
            if value is not None:
                try:
                    value = related_model.objects.get(pk=value)
                except related_model.DoesNotExist:
                    raise WriteError(
                        f'Related {related_model.__name__} {value} not found'
                    )
        resolved[field_name] = value
    return resolved


def _atomic_save(channel, model_class, instance_id, data) -> Model:
    """Load, authorize, and save an instance inside a single transaction.

    Uses ``select_for_update()`` to hold a row-level lock from the moment
    the instance is loaded until the transaction commits, preventing
    concurrent modifications between the authorization check and save.
    """
    with transaction.atomic():
        try:
            instance = model_class.objects.select_for_update().get(
                pk=instance_id,
            )
        except model_class.DoesNotExist:
            raise WriteError(f'Instance {instance_id} not found')

        if not channel.can_save(instance, data):
            logger.warning(
                'write denied: save on %s:%s by user %s (authorization) [%s]',
                model_class.__name__, instance_id, channel.user_id, channel.__class__.__name__,
            )
            raise ForbiddenError('Save operation not permitted')

        resolved = _resolve_fk_values(model_class, data)
        for field_name, value in resolved.items():
            setattr(instance, field_name, value)

        instance.save()
        return instance


def _atomic_create(channel, child_model, parent_model, parent_id,
                   relation_name, data) -> Model:
    """Load parent, authorize, and create a child inside a single transaction.

    Uses ``select_for_update()`` on the parent to hold a row-level lock,
    preventing concurrent modifications between the authorization check
    and create.
    """
    with transaction.atomic():
        try:
            parent = parent_model.objects.select_for_update().get(
                pk=parent_id,
            )
        except parent_model.DoesNotExist:
            raise WriteError(f'Parent instance {parent_id} not found')

        if not channel.can_create(child_model, parent, data):
            logger.warning(
                'write denied: create %s under %s:%s by user %s (authorization) [%s]',
                child_model.__name__, parent_model.__name__, parent_id,
                channel.user_id, channel.__class__.__name__,
            )
            raise ForbiddenError('Create operation not permitted')

        resolved = _resolve_fk_values(child_model, data)
        manager = getattr(parent, relation_name)
        return manager.create(**resolved)


def _atomic_delete(channel, model_class, instance_id) -> None:
    """Load, authorize, and delete an instance inside a single transaction.

    Uses ``select_for_update()`` to hold a row-level lock from the moment
    the instance is loaded until the transaction commits.
    """
    with transaction.atomic():
        try:
            instance = model_class.objects.select_for_update().get(
                pk=instance_id,
            )
        except model_class.DoesNotExist:
            raise WriteError(f'Instance {instance_id} not found')

        if not channel.can_delete(instance):
            logger.warning(
                'write denied: delete on %s:%s by user %s (authorization) [%s]',
                model_class.__name__, instance_id, channel.user_id, channel.__class__.__name__,
            )
            raise ForbiddenError('Delete operation not permitted')

        instance.delete()


async def execute_save(
    channel,
    instance_type: str,
    instance_id: int,
    data: dict[str, Any],
) -> Model:
    """Execute a save operation after authorization.

    Args:
        channel: The ContextChannel instance
        instance_type: The _instance_type string of the serializer
        instance_id: The ID of the instance to update
        data: The partial field dict to apply

    Returns:
        The updated model instance

    Raises:
        WriteError: If instance not found or operation fails
        ForbiddenError: If can_save returns False
    """
    state_model = channel._state_model
    model_nodes = state_model.index.get(instance_type, [])
    if not model_nodes:
        raise WriteError(f'Unknown instance type: {instance_type}')

    _verify_writable(channel, instance_type, SAVE)

    model_class = model_nodes[0].model
    allowed_fields = _get_writable_fields(model_nodes[0])
    data = {k: v for k, v in data.items() if k in allowed_fields}
    data = _validate_data(model_nodes[0], data, partial=True)

    await _verify_in_context(channel, instance_type, instance_id)

    try:
        result = await sync_to_async(_atomic_save)(
            channel, model_class, instance_id, data,
        )
    except (WriteError, ForbiddenError):
        raise
    except Exception:
        logger.error(
            'write error: save on %s:%s by user %s [%s]',
            instance_type, instance_id, channel.user_id, channel.__class__.__name__,
            exc_info=True,
        )
        raise

    logger.debug(
        'write ok: save on %s:%s by user %s [%s]',
        instance_type, instance_id, channel.user_id, channel.__class__.__name__,
    )
    return result


async def execute_create(
    channel,
    instance_type: str,
    parent_type: str,
    parent_id: int,
    relation_name: str,
    data: dict[str, Any],
) -> Model:
    """Execute a create operation after authorization.

    Args:
        channel: The ContextChannel instance
        instance_type: The _instance_type string of the child serializer
        parent_type: The _instance_type string of the parent serializer
        parent_id: The ID of the parent instance
        relation_name: The name of the relation field on the parent
        data: The field dict for the new instance

    Returns:
        The created model instance

    Raises:
        WriteError: If parent not found or operation fails
        ForbiddenError: If can_create returns False
    """
    state_model = channel._state_model
    child_nodes = state_model.index.get(instance_type, [])
    if not child_nodes:
        raise WriteError(f'Unknown instance type: {instance_type}')
    child_model = child_nodes[0].model

    _verify_writable(channel, instance_type, CREATE)

    allowed_fields = _get_writable_fields(child_nodes[0])
    data = {k: v for k, v in data.items() if k in allowed_fields}
    data = _validate_data(child_nodes[0], data, partial=True)

    parent_nodes = state_model.index.get(parent_type, [])
    if not parent_nodes:
        raise WriteError(f'Unknown parent type: {parent_type}')
    parent_model = parent_nodes[0].model

    # Validate relation_name against the parent's declared children
    parent_children = getattr(parent_nodes[0], 'children', {})
    if relation_name not in parent_children:
        raise WriteError(
            f'{relation_name} is not a declared relation on {parent_type}'
        )
    expected_child = parent_children[relation_name]
    if expected_child.instance_type != instance_type:
        raise WriteError(
            f'Relation {relation_name} type {expected_child.instance_type} '
            f'does not match requested type {instance_type}'
        )

    # Enforce user_key: always set to the current user, ignoring client value
    user_key = child_nodes[0].user_key
    if user_key:
        data[user_key] = channel.user_id

    await _verify_in_context(channel, parent_type, parent_id)

    try:
        result = await sync_to_async(_atomic_create)(
            channel, child_model, parent_model, parent_id,
            relation_name, data,
        )
    except (WriteError, ForbiddenError):
        raise
    except Exception:
        logger.error(
            'write error: create %s under %s:%s by user %s [%s]',
            instance_type, parent_type, parent_id, channel.user_id, channel.__class__.__name__,
            exc_info=True,
        )
        raise

    logger.debug(
        'write ok: create %s under %s:%s by user %s [%s]',
        instance_type, parent_type, parent_id, channel.user_id, channel.__class__.__name__,
    )
    return result


async def execute_delete(
    channel,
    instance_type: str,
    instance_id: int,
) -> None:
    """Execute a delete operation after authorization.

    Args:
        channel: The ContextChannel instance
        instance_type: The _instance_type string of the serializer
        instance_id: The ID of the instance to delete

    Raises:
        WriteError: If instance not found
        ForbiddenError: If can_delete returns False
    """
    state_model = channel._state_model
    model_nodes = state_model.index.get(instance_type, [])
    if not model_nodes:
        raise WriteError(f'Unknown instance type: {instance_type}')

    _verify_writable(channel, instance_type, DELETE)

    model_class = model_nodes[0].model

    await _verify_in_context(channel, instance_type, instance_id)

    try:
        await sync_to_async(_atomic_delete)(channel, model_class, instance_id)
    except (WriteError, ForbiddenError):
        raise
    except Exception:
        logger.error(
            'write error: delete on %s:%s by user %s [%s]',
            instance_type, instance_id, channel.user_id, channel.__class__.__name__,
            exc_info=True,
        )
        raise

    logger.debug(
        'write ok: delete on %s:%s by user %s [%s]',
        instance_type, instance_id, channel.user_id, channel.__class__.__name__,
    )
