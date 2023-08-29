from collections import namedtuple, deque
from rest_framework import serializers
from django.db import models, connection
from django.db import ProgrammingError
from django.db.models.fields import related_descriptors
from .ts import export_interface


class StateModel:
    """Extracts the model from a nested serializer hierarchy and setup structure
    for serializing each instance isolated to build the structure on the
    frontend"""

    def __init__(self, state_serializer, many=False, origin=None, instance_property=None, query_property=None, reverse_acessor=None):
        self.nested_serializer = state_serializer
        self.many = many
        self.origin = origin
        self.reverse_acessor = reverse_acessor

        if origin is None:
            self.anchor = self
            self.instance_path = []
            self.query_path = []
            self.index = {}
        else:
            self.anchor = origin.anchor
            self.instance_path = origin.instance_path[:] + [instance_property]
            self.query_path = origin.query_path[:] + [query_property]
            self.index = origin.index

        meta = state_serializer.Meta
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

        self.index[self.instance_type] = self

        self.user_key = getattr(meta, 'user_key', None)
        self.optimistic = getattr(meta, 'optimistic', False)
        self.optimistic_timeout = getattr(meta, 'optimistic_timeout', 3)

        self.flat_serializer, fields = _disassemble_nested(self.nested_serializer)

        self.children = {}
        for field_name, serializer in fields.items():
            node = self._build_child(field_name, serializer)
            if node:
                self.children[field_name] = node

        # Track this serializer to generate interface.ts file
        export_interface(self.nested_serializer.__class__)

    def __getitem__(self, key):
        return self.children[key]

    def __iter__(self):
        return self.index.values().__iter__()

    def frontend_model(self):
        frontend = {}
        for key, node in self.index.items():
            frontend[key] = instance_model = {}
            serializer = node.nested_serializer
            for field_name, field in serializer._declared_fields.items():
                if is_model_serializer(field):
                    instance_model[field_name] = node[field_name].instance_type

        return frontend

    def get_anchors(self, serialized):
        """Get all anchors that should receive an instance"""
        peer_type = serialized['_instance_type']
        peer_model = self.index[peer_type]
        anchor_key = peer_model.anchor_key
        kwargs = {anchor_key: serialized['id']}
        return self.model.objects.filter(**kwargs)

    def serialize_instance(self, instance, tstamp):
        data = self.flat_serializer(instance).data
        return self._mark(data, tstamp)

    def serialize_delete(self, instance, tstamp):
        data = _mark({'id': instance.pk})
        data['_deleted'] = True
        data['_operation'] = 'delete'
        return data

    def serialize_state(self, instance, tstamp):
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

    def _mark(self, serialized, tstamp):
        serialized['_instance_type'] = self.instance_type
        serialized['_tstamp'] = tstamp
        serialized['_user_key'] = self.user_key
        serialized['_operation'] = 'initial_state'
        return serialized

    def _build_child(self, field_name, serializer):
        try:
            descriptor = getattr(self.model, field_name)
        except AttributeError:
            return

        if isinstance(descriptor, related_descriptors.ReverseManyToOneDescriptor):
            related_descriptor = getattr(descriptor.field.model, descriptor.field.name)
            return StateModel(
                serializer.child,
                True,
                self,
                field_name,
                descriptor.field.name,#related_descriptor.field.related_query_name(),
                descriptor.field.name,
            )

        if isinstance(descriptor, related_descriptors.ForwardManyToOneDescriptor):
            return StateModel(
                serializer,
                False,
                self,
                field_name,
                field_name,
                descriptor.field.related_query_name(),
            )

        if isinstance(descriptor, related_descriptors.ReverseOneToOneDescriptor):
            return StateModel(
                serializer,
                False,
                self,
                field_name,
                field_name,
                descriptor.related.get_related_field().name,
            )


def _disassemble_nested(nested_serializer):
    """Removes nested serializer fields to build a flat serializer.
    Returns a flat serializer class and dictionary with removed properties.
    """
    serializer_fields = {}
    declared_fields = {}

    for field_name in nested_serializer.Meta.fields:
        field = nested_serializer._declared_fields.get(field_name)

        if field is None:
            continue

        if is_model_serializer(field):
            serializer_fields[field_name] = field
        else:
            declared_fields[field_name] = field

    # Construct the flat serializer Meta class
    class Meta:
        model = nested_serializer.Meta.model
        fields = nested_serializer.Meta.fields

    # Return the new flat serializer class and the dictionary of substituted fields
    FlatSerializer = type(
        nested_serializer.__class__.__name__,
        (serializers.ModelSerializer,),
        {"Meta": Meta, **declared_fields}
    )
    return FlatSerializer, serializer_fields


def is_model_serializer(field):
    try:
        # If this is a queryset serializer, check for child serializer
        return isinstance(field.child, serializers.ModelSerializer)
    except AttributeError:
        # No child property, we expect a ModelSerializer
        return isinstance(field, serializers.ModelSerializer)
