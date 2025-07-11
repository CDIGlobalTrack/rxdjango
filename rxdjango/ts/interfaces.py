import os
import typing
import importlib
from django.utils import timezone
from django.db.models.fields import related_descriptors
from django.conf import settings
from rest_framework import serializers, relations, fields
from . import ts_exported, header, interface_name, get_ts_type, diff

def create_app_interfaces(app, apply_changes=True, force=False):
    serializer_module_name = f'{app}.serializers'
    serializer_path = serializer_module_name.replace('.', '/') + '.py'

    ts_file_path = os.path.join(settings.RX_FRONTEND_DIR, f'{app}/{app}.interfaces.d.ts')
    py_mtime = None

    if os.path.exists(serializer_path):
        py_mtime = os.path.getmtime(serializer_path)

    existing = []

    if os.path.exists(ts_file_path):
        if not force and py_mtime == os.path.getmtime(ts_file_path):
            return
        with open(ts_file_path, 'r') as file:
            existing = file.read().split('\n')

    try:
        module = importlib.import_module(serializer_module_name)
    except ModuleNotFoundError:
        return

    serializers = get_serializers(module)

    try:
        module_serializers = serializers.pop(app)
    except KeyError:
        return

    code = header(
        app,
        f'Based on {app}/serializers.py',
    )

    initial_length = len(code)

    code.append(f"import {{ InstanceType }} from '@rxdjango/react';\n")

    imports = []
    for external_app, dependencies in serializers.items():
        dependencies = [dep for dep in dependencies if ts_exported(dep)]
        if not dependencies:
            continue
        interfaces = [interface_name(d) for d in dependencies]
        imports.append(''.join([
            'import { ',
            ', '.join(sorted(interfaces)),
            ' } from ',
            f"'../{external_app}/{external_app}.interfaces';",
        ]))

    code += sorted(imports)

    code.append('')  # line break

    for Serializer in module_serializers:
        if ts_exported(Serializer):
            code.append(serialize_type(Serializer))

    if len(code) == initial_length + 2:
        return

    content = '\n'.join(code)

    if content.split('\n')[2:] == existing[2:]:
        return

    difference = diff(existing, content.split('\n'), ts_file_path)
    if not apply_changes:
        return difference

    try:
        with open(ts_file_path, 'w') as fh:
            fh.write(content)
    except FileNotFoundError:
        os.makedirs(os.path.dirname(ts_file_path), exist_ok=True)
        with open(ts_file_path, 'w') as fh:
            fh.write(content)

    if py_mtime:
        os.utime(ts_file_path, (py_mtime, py_mtime))

    return difference


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

    return get_ts_type(ftype)

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

        # Determine if field is auto filled
        field = getattr(serializer.Meta.model, key, None)
        if field:
            try:
                field = field.field
                auto = getattr(field, 'auto_now', False) or getattr(field, 'auto_now_add', False)
            except AttributeError:
                pass
        else:
            auto = False

        if key != 'id':
            if value.required:
                property = property + "?"

            if value.allow_null and not auto:
                type = type + " | null"

        ts_fields.append(f"    {property}: {type};")
    collapsed_fields = '\n'.join(ts_fields)
    return f'export interface {name} extends InstanceType {{\n{collapsed_fields}\n}}\n'
