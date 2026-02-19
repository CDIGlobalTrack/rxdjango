from __future__ import annotations

from collections import defaultdict
from typing import Any, Generator, Iterator

from rest_framework import serializers
from django.db import models, connection
from django.db import ProgrammingError
from django.db.models import Model
from django.db.models.fields import related_descriptors
from .ts import export_interface
from .exceptions import UnknownProperty
from .related_properties import is_related_property, get_accessor, get_reverse_accessor

class StateModel:
    """The StateModel is constructed based on a nested serializers.ModelSerializer
    instance. It introspects the serializer and recursively build a model with all
    layers of the serializer.
    """

    def __init__(self, state_serializer: serializers.ModelSerializer, active_flag: str | None, many: bool = False, origin: StateModel | None = None, instance_property: str | None = None, query_property: str | None = None, reverse_acessor: str | None = None) -> None:
        self.nested_serializer = state_serializer
        self.many = many
        self.origin = origin
        self.reverse_acessor = reverse_acessor
        self.active_flag = active_flag

        if origin is None:
            # This is the top-most call, this layer is the anchor
            self.anchor = self
            self.instance_path = []
            self.query_path = []
            self.index = defaultdict(list)
        else:
            self.anchor = origin.anchor
            self.instance_path = origin.instance_path[:] + [instance_property]
            self.query_path = origin.query_path[:] + [query_property]
            self.index = origin.index

        try:
            meta = state_serializer.Meta
        except AttributeError:
            meta = state_serializer.child.Meta

        self.model = meta.model

        if origin:
            self.anchor_key = '__'.join(self.query_path)
        else:
            self.anchor_key = self.model._meta.pk.name

        self.instance_type = '.'.join([
            self.nested_serializer.__module__,
            self.nested_serializer.__class__.__name__,
        ])
        #modu = self.nested_serializer.__module__.replace('.serializers', '')
        #print(f"""perl -pi -e "s/instance_type === '{modu}.{self.model.__name__}/instance_type === '{self.instance_type}/" $1""")

        self.index[self.instance_type].append(self)

        self.user_key = getattr(meta, 'user_key', None)
        if self.user_key and self.user_key not in meta.fields:
            raise ProgrammingError(f'{state_serializer.__class__.__name__}.Meta declares user_key="{self.user_key}", but "{self.user_key}" is not in fields')
        self.optimistic = getattr(meta, 'optimistic', False)
        self.optimistic_timeout = getattr(meta, 'optimistic_timeout', 3)

        self.flat_serializer, fields = self._disassemble_nested()

        self.children = {}
        for field_name, serializer in fields.items():
            node = self._build_child(field_name, serializer)
            if node:
                self.children[field_name] = node

        export_interface(self.nested_serializer.__class__)

    def __str__(self) -> str:
        return f'StateModel for {self.instance_type}'

    def __repr__(self) -> str:
        return str(self)

    def __getitem__(self, key: str) -> StateModel:
        return self.children[key]

    def models(self) -> Iterator[StateModel]:
        for models in self.index.values():
            for model in models:
                yield model

    def frontend_model(self) -> dict[str, dict[str, str]]:
        frontend = {}
        for key, nodes in self.index.items():
            node = nodes[0]
            instance_model = {}
            frontend[key] = instance_model
            serializer = node.nested_serializer
            for field_name, field in serializer._declared_fields.items():
                if is_model_serializer(field):
                    instance_model[field_name] = node[field_name].instance_type

        return frontend

    def get_anchors(self, serialized: dict[str, Any]) -> Iterator[Model]:
        """Get all anchors that should receive an instance"""
        peer_type = serialized['_instance_type']
        peer_models = self.index[peer_type]
        for peer_model in peer_models:
            anchor_key = peer_model.anchor_key
            kwargs = {anchor_key: serialized['id']}
            if self.active_flag:
                kwargs[self.active_flag] = True
            for instance in self.anchor.model.objects.filter(**kwargs):
                yield instance

    def clean_active(self) -> None:
        if not self.active_flag:
            return
        kwargs = {self.active_flag: False}
        self.anchor.model.objects.update(**kwargs)

    def serialize_instance(self, instance: Model, tstamp: float) -> dict[str, Any]:
        data = self.flat_serializer(instance).data
        data['_deleted'] = False
        return self._mark(data, tstamp)

    def serialize_delete(self, instance: Model, tstamp: float) -> dict[str, Any]:
        pk = instance.pk or instance.id
        data = self._mark({'id': pk}, tstamp)
        data['_deleted'] = True
        data['_operation'] = 'delete'
        return data

    def serialize_state(self, instance: Model, tstamp: float) -> Generator[list[dict[str, Any]], None, None]:
        if self.many:
            data = self.flat_serializer(instance.all(), many=True).data
            instances = instance.all()
        else:
            data = [ self.flat_serializer(instance).data ]
            instances = [instance]

        for serialized in data:
            self._mark(serialized, tstamp)

        yield data

        for field_name, peer_model in self.children.items():
            for instance in instances:
                try:
                    peer_instance = getattr(instance, field_name)
                except AttributeError:
                    continue
                if peer_instance is None:
                    continue
                for serialized in peer_model.serialize_state(peer_instance, tstamp):
                    yield serialized

    def _mark(self, serialized: dict[str, Any], tstamp: float) -> dict[str, Any]:
        serialized['_instance_type'] = self.instance_type
        serialized['_tstamp'] = tstamp
        serialized['_operation'] = 'initial_state'
        if self.user_key:
            serialized['_user_key'] = serialized.get(self.user_key, None)
        else:
            serialized['_user_key'] = None
        return serialized

    def _disassemble_nested(self) -> tuple[type[serializers.ModelSerializer], dict[str, serializers.BaseSerializer]]:
        serializer_fields = {}
        declared_fields = {}

        for field_name in self.nested_serializer.fields.keys():
            field = self.nested_serializer._declared_fields.get(field_name)

            if field is None:
                continue

            if is_related_property(self.model, field_name):
                try:
                    new_field = serializers.PrimaryKeyRelatedField(
                        many=field.many, read_only=True,
                    )
                except AttributeError:
                    new_field = serializers.PrimaryKeyRelatedField(
                        read_only=True,
                    )
                serializer_fields[field_name] = field
                declared_fields[field_name] = new_field
            elif is_model_serializer(field):
                serializer_fields[field_name] = field
            else:
                declared_fields[field_name] = field

        # Return the new flat serializer class and the dictionary of substituted fields
        FlatSerializer = type(
            self.nested_serializer.__class__.__name__,
            (serializers.ModelSerializer,),
            {
                "Meta": self.nested_serializer.Meta,
                **declared_fields,
            }
        )

        return FlatSerializer, serializer_fields

    def _build_child(self, field_name: str, serializer: serializers.BaseSerializer) -> StateModel | None:
        try:
            descriptor = getattr(self.model, field_name)
        except AttributeError:
            return

        if isinstance(descriptor, related_descriptors.ManyToManyDescriptor):
            related_descriptor = getattr(descriptor.field.model, descriptor.field.name)
            return StateModel(
                serializer.child,
                None,
                True,
                self,
                field_name,
                field_name,
                descriptor.field.name,
            )

        if isinstance(descriptor, related_descriptors.ReverseManyToOneDescriptor):
            related_descriptor = getattr(descriptor.field.model, descriptor.field.name)
            return StateModel(
                serializer.child,
                None,
                True,
                self,
                field_name,
                related_descriptor.field.related_query_name(),
                descriptor.field.name,
            )

        if isinstance(descriptor, related_descriptors.ForwardManyToOneDescriptor):
            return StateModel(
                serializer,
                None,
                False,
                self,
                field_name,
                field_name,
                descriptor.field.related_query_name(),
            )

        if isinstance(descriptor, related_descriptors.ReverseOneToOneDescriptor):
            return StateModel(
                serializer,
                None,
                False,
                self,
                field_name,
                field_name,
                descriptor.related.remote_field.name,
            )

        if isinstance(descriptor, property):
            query_property = get_accessor(self.model, field_name)
            reverse_acessor = get_reverse_accessor(self.model, field_name)
            if not query_property:
                my_module = self.__class__.__module__
                raise UnknownProperty(
                    f'Unknown property {field_name} in '
                    f'{self.model.__module__}.{self.model.__name__}.\n'
                    f'Use {my_module}.decorators.related_property '
                    'to provide an acessor'
                )

            many = getattr(serializer, 'many', False)
            return StateModel(
                serializer.child if many else serializer,
                None,
                many,
                self,
                field_name,
                query_property,
                reverse_acessor,
            )


def is_model_serializer(field: serializers.BaseSerializer) -> bool:
    try:
        return isinstance(field.child, serializers.ModelSerializer)
    except AttributeError:
        return isinstance(field, serializers.ModelSerializer)
