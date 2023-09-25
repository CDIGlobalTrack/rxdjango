import os
import types
import typing
import importlib
from decimal import Decimal
from datetime import datetime
from django.utils import timezone
from django.db.models.query import QuerySet
from django.db.models.fields import related_descriptors
from django.conf import settings
from rest_framework import serializers, relations, fields
from . import ts_exported, header, interface_name


def create_app_interfaces(app):
    try:
        module = importlib.import_module(f'{app}.serializers')
    except ModuleNotFoundError:
        return

    path = os.path.join(settings.RX_FRONTEND_DIR, f'{app}/{app}.interfaces.d.ts')

    serializers = get_serializers(module)

    try:
        module_serializers = serializers.pop(app)
    except KeyError:
        return

    now = timezone.now().strftime('%Y-%m-%d %H:%M')
    tz = timezone.now().tzinfo

    code = header(
        app,
        f'Based on {app}/serializers.py',
    )

    initial_length = len(code)

    code.append(f"import {{ InstanceType }} from 'lib/django-react';\n")

    for external_app, dependencies in serializers.items():
        dependencies = [ dep for dep in dependencies if ts_exported(dep) ]
        if not dependencies:
            continue
        code.append(''.join([
            'import { ',
            ', '.join([ interface_name(d) for d in dependencies ]),
            ' } from ',
            f"'../{external_app}/{external_app}.interfaces';",
            ]))

    code.append('')  # line break

    for Serializer in module_serializers:
        if ts_exported(Serializer):
            code.append(serialize_type(Serializer))

    if len(code) == initial_length + 1:
        return

    content = '\n'.join(code)

    try:
        fh = open(path, 'w')
    except FileNotFoundError:
        os.mkdir(os.path.dirname(path))
        fh = open(path, 'w')
    fh.write(content)
    fh.close()

def get_serializers(module):
    apps = {}

    for klass in _get_serializer_classes(module):
        app = klass.__module__.split('.')[0]
        try:
            apps[app].append(klass)
        except KeyError:
            apps[app] = [klass]
    return apps

def _get_serializer_classes(module):
    for name, klass in module.__dict__.items():
        try:
            if issubclass(klass, serializers.Serializer):
                yield klass
        except TypeError:
            pass

mappings = {
    serializers.BooleanField: 'boolean',
    serializers.CharField: 'string',
    serializers.EmailField: 'string',
    serializers.RegexField: 'string',
    serializers.SlugField: 'string',
    serializers.URLField: 'string',
    serializers.UUIDField: 'string',
    serializers.FilePathField: 'string',
    serializers.IPAddressField: 'string',
    serializers.IntegerField: 'number',
    serializers.FloatField: 'number',
    serializers.DecimalField: 'number',
    serializers.DateTimeField: 'string',
    serializers.DateField: 'string',
    serializers.TimeField: 'string',
    serializers.DurationField: 'string',
    serializers.DictField: 'Map',
    relations.PrimaryKeyRelatedField: 'number',
}


def __process_field(field_name, field, serializer):
    '''
    Generates and returns a tuple representing the Typescript field name and Type.
    '''
    if hasattr(field, 'child'):
        is_many = True
        field_type = type(field.child)
    elif hasattr(field, 'child_relation'):
        is_many = True
        field_type = type(field.child_relation)
    else:
        is_many = False
        field_type = type(field)

    ts_type = mappings.get(field_type)

    if not ts_type:
        if hasattr(field, 'choices'):
            ts_type = __map_choices_to_union(field_type, field.choices)
        elif field_type is fields.ReadOnlyField:
            model_field = getattr(serializer.Meta.model, field_name)
            if isinstance(model_field,
                          related_descriptors.ForeignKeyDeferredAttribute):
                ts_type = 'number'
            else:
                func = model_field
                if type(model_field) is property:
                    func = func.fget
                ts_type = __get_function_type(func)

        else:
            if field_type.__name__.endswith('Serializer'):
                ts_type = interface_name(field_type)
            else:
                ts_type = 'any'


    if is_many:
        ts_type += '[]'

    return (field_name, ts_type)

def __get_function_type(func):
    hints = typing.get_type_hints(func)
    try:
        ftype = hints['return']
    except KeyError:
        return 'any'

    PY_TO_TS = {
        int: 'number',
        float: 'number',
        Decimal: 'number',
        datetime: 'string',
        str: 'string',
        bool: 'boolean',
        type(None): 'null',
        QuerySet: 'number[]',
    }

    if type(ftype) is types.UnionType :
        py_types = typing.get_args(ftype)
    else:
        py_types = [ftype]

    ts_types = [ PY_TO_TS[typ] for typ in py_types ]
    return ' | '.join(ts_types)

def __map_choices_to_union(field_type, choices):
    '''
    Generates and returns a TS union type for all values in the provided choices OrderedDict
    '''
    if not choices:
        return 'any'

    return ' | '.join(f'"{key}"' if type(key) == str else str(key) for key in choices.keys())


def serialize_type(serializer):
    '''
    Generates and returns a Typescript Interface by iterating
    through the serializer fields of the DRF Serializer class
    passed in as a parameter, and mapping them to the appropriate Typescript
    data type.
    '''
    name = serializer.__name__.replace('Serializer', 'Type')
    fields = []
    if hasattr(serializer, 'get_fields'):
        instance = serializer()
        fields = instance.get_fields().items()
    else:
        try:
            fields = serializer._declared_fields.items()
        except AttributeError:
            return
    ts_fields = []
    for key, value in fields:
        property, type = __process_field(key, value, serializer)

        if key != 'id':
            if value.required:
                property = property + "?"

            if value.allow_null and not value.read_only:
                type = type + " | null"

        ts_fields.append(f"    {property}: {type};")
    collapsed_fields = '\n'.join(ts_fields)
    return f'export interface {name} extends InstanceType {{\n{collapsed_fields}\n}}\n'
