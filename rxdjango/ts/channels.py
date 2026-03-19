import os
import re
import json
import typing
from collections import defaultdict
from channels.routing import ProtocolTypeRouter, URLRouter
from django.conf import settings
from rxdjango.consumers import StateConsumer
from rxdjango.actions import list_actions
from rxdjango.operations import Operation
from . import (header, interface_name, diff, get_ts_type, snake_to_camel,
               TYPEMAP)
from .interfaces import mappings as _field_type_mappings


def create_app_channels(app, apply_changes=True, force=False):
    consumer_urlpatterns = list_consumer_patterns(app)
    if not consumer_urlpatterns:
        return

    channel_module_name = f'{app}.channels'
    channel_path = channel_module_name.replace('.', '/') + '.py'

    ts_file_path = os.path.join(settings.RX_FRONTEND_DIR, f'{app}/{app}.channels.ts')
    py_mtime = None

    if os.path.exists(channel_path):
        py_mtime = os.path.getmtime(channel_path)

    existing = []

    if os.path.exists(ts_file_path):
        if not force and py_mtime == os.path.getmtime(ts_file_path):
            return
        with open(ts_file_path, 'r') as file:
            existing = file.read().split('\n')

    code = header(
        app,
        f'Based on all ContextChannel.as_asgi() calls in {settings.ASGI_APPLICATION}',
        f'This is expected to match {app}/channels.py',
    )

    import_types = defaultdict(list)
    body = []
    has_writable = False

    for urlpattern in consumer_urlpatterns:
        consumer_class = urlpattern.callback.consumer_class
        context_channel_class = consumer_class.context_channel_class
        class_code, writable_used = generate_ts_class(
            context_channel_class, urlpattern, import_types,
        )
        has_writable = has_writable or writable_used
        body.append('\n')
        body.extend(class_code)

    if not body:
        return

    if has_writable:
        rxdjango_imports = 'ContextChannel, Saveable, Deleteable, Creatable'
    else:
        rxdjango_imports = 'ContextChannel'
    code.extend([
        f"import {{ {rxdjango_imports} }} from '@rxdjango/react';\n",
        f'const SOCKET_URL = {settings.RX_WEBSOCKET_URL};',
    ])

    code.append('')  # line break
    code.extend(build_imports(import_types, app))
    code.extend(body)

    content = '\n'.join(code)

    if content.split('\n')[2:] == existing[2:]:
        if py_mtime:
            os.utime(ts_file_path, (py_mtime, py_mtime))
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


def get_root_routing():
    asgi_app = settings.ASGI_APPLICATION
    module_name, app_name = asgi_app.rsplit('.', 1)
    module = __import__(module_name, fromlist=[app_name])
    return getattr(module, app_name)


def list_consumer_patterns(app_name, pattern_list=None, router=None):
    """
    Function to extract WebSocket consumers from the root routing (or provided router)
    for a specific app that extends the StateConsumer class.
    """

    if pattern_list is None:
        pattern_list = []
        router = get_root_routing()

    try:
        router = router.application
    except AttributeError:
        pass

    if isinstance(router, ProtocolTypeRouter):
        # Extract the websocket routing
        websocket_router = router.application_mapping.get('websocket', None)
        if websocket_router:
            list_consumer_patterns(app_name, pattern_list, websocket_router)

    elif isinstance(router, URLRouter):
        for route in router.routes:
            callback = route.callback
            if not hasattr(callback, 'consumer_class') or \
               not issubclass(callback.consumer_class, StateConsumer):
                continue

            context_channel_class = callback.consumer_class.context_channel_class

            if context_channel_class.__module__.startswith(f'{app_name}.'):
                pattern_list.append(route)

            elif isinstance(route, URLRouter):
                # Recursive exploration for nested URLRouter objects
                list_consumer_patterns(app_name, pattern_list, route)

    return pattern_list


def pattern_to_ts(urlpattern):
    pattern_str = str(urlpattern.pattern)

    # Extract key-type pairs
    matches = re.findall(r'<(\w+?):(\w+?)>', pattern_str)

    # Basic mapping from Django path converters to TypeScript types
    type_mapping = {
        'str': 'string',
        'int': 'number',
        'slug': 'string',
        'uuid': 'string',
        'path': 'string',
        # Add more if needed
    }

    parameters = {key: type_mapping.get(type_, 'any') for type_, key in matches}

    # Convert Django pattern to a format easier to process in JS
    endpoint = re.sub(r'<(\w+?):(\w+?)>', r'{\2}', pattern_str)

    return endpoint, parameters


def build_imports(serializers, this_app):
    code = []
    for app, app_serializers in serializers.items():
        interfaces = sorted(set(interface_name(seri) for seri in app_serializers))
        interfaces = ', '.join(interfaces)
        path = '.' if app == this_app else f'../{app}'
        code.append(f"import {{ {interfaces} }} from '{path}/{app}.interfaces.d';")

    return code


def _writable_type_name(serializer_class):
    """Generate a writable type alias name from a serializer class.

    e.g. TaskSerializer -> WritableTask
    """
    return 'Writable' + interface_name(serializer_class).replace('Type', '')


def _payload_type_name(serializer_class):
    """Generate a payload type alias name from a serializer class.

    e.g. TaskSerializer -> TaskPayload
    """
    return interface_name(serializer_class).replace('Type', 'Payload')


def _get_payload_fields(model_node):
    """Return (field_name, ts_type) pairs for writable fields on a serializer.

    Applies the same filtering as ``_get_writable_fields`` in write.py:
    excludes ``id``, read-only fields, and many=True nested serializers.

    Single nested serializers (representing ForeignKey fields) are mapped to
    ``number`` because the client sends the FK integer ID.
    """
    from rest_framework import serializers as drf_serializers

    serializer = model_node.nested_serializer
    fields = []
    for name, field in serializer.fields.items():
        if name == 'id':
            continue
        if getattr(field, 'read_only', False):
            continue
        is_many_nested = (isinstance(field, drf_serializers.BaseSerializer) and
                          getattr(field, 'many', False))
        if is_many_nested:
            continue

        # Determine TS type
        if isinstance(field, drf_serializers.BaseSerializer):
            # Single nested serializer (FK) — client sends integer ID
            ts_type = 'number'
        elif hasattr(field, 'child'):
            child_type = type(field.child)
            ts_type = _field_type_mappings.get(child_type, 'any') + '[]'
        elif hasattr(field, 'child_relation'):
            child_type = type(field.child_relation)
            ts_type = _field_type_mappings.get(child_type, 'any') + '[]'
        else:
            # Check mappings first (e.g. PrimaryKeyRelatedField -> number),
            # then fall back to choices for unmapped field types.
            mapped = _field_type_mappings.get(type(field))
            if mapped:
                ts_type = mapped
            elif hasattr(field, 'choices') and field.choices:
                ts_type = ' | '.join(
                    f'"{k}"' if isinstance(k, str) else str(k)
                    for k in field.choices.keys()
                )
            else:
                ts_type = 'any'

        if getattr(field, 'allow_null', False):
            ts_type = f'{ts_type} | null'

        fields.append((name, ts_type))
    return fields


def _generate_payload_lines(payload_name, model_node):
    """Generate TypeScript lines for a payload type.

    Example output::

        export type TaskPayload = {
          name?: string;
          developer?: number;
        };
    """
    fields = _get_payload_fields(model_node)
    lines = [f'export type {payload_name} = {{']
    for field_name, ts_type in fields:
        lines.append(f'  {field_name}?: {ts_type};')
    lines.append('};')
    return lines


def _build_writable_types(state_model, writable, import_types):
    """Walk the state model tree and generate channel-specific writable types.

    Returns (type_lines, state_type_name_or_none) where type_lines is a list
    of TypeScript type alias lines to emit before the class, and
    state_type_name_or_none is the channel-specific state type name if any
    writable types exist, or None if the channel is fully read-only.
    """
    if not writable:
        return [], None

    # Collect which instance_types are writable and their operations
    # writable is { instance_type_str: [ops] }
    # We need to map instance_type -> (serializer_class, ops)
    # The state_model.index maps instance_type -> [StateModel nodes]
    writable_info = {}  # instance_type -> (interface_name, writable_type_name, payload_name, ops)
    for instance_type, ops in writable.items():
        nodes = state_model.index.get(instance_type, [])
        if not nodes:
            continue
        serializer_class = nodes[0].nested_serializer.__class__
        iface_name = interface_name(serializer_class)
        wtype_name = _writable_type_name(serializer_class)
        ptype_name = _payload_type_name(serializer_class)
        writable_info[instance_type] = (iface_name, wtype_name, ptype_name, ops)

        # Ensure the interface is imported
        app = serializer_class.__module__.split('.')[0]
        import_types[app].append(serializer_class)

    if not writable_info:
        return [], None

    # For each writable type, check if it has children that are also writable.
    # If so, the writable type needs Omit to replace those relation fields.
    type_lines = []
    # Track which writable types have writable children (need Omit)
    writable_children = {}  # instance_type -> { field_name: child_instance_type }
    for instance_type in writable_info:
        nodes = state_model.index.get(instance_type, [])
        if not nodes:
            continue
        node = nodes[0]
        wchildren = {}
        for field_name, child_node in node.children.items():
            if child_node.instance_type in writable_info:
                wchildren[field_name] = child_node.instance_type
        if wchildren:
            writable_children[instance_type] = wchildren

    # Emit payload types first — one per writable serializer
    for instance_type in writable_info:
        nodes = state_model.index.get(instance_type, [])
        if not nodes:
            continue
        _, _, ptype_name, _ = writable_info[instance_type]
        type_lines.extend(_generate_payload_lines(ptype_name, nodes[0]))
        type_lines.append('')

    # The anchor type's writable alias is redundant — the state type covers it.
    # Skip emitting it separately.
    anchor_type = state_model.instance_type

    # Emit writable type aliases (leaf types first, then types with writable children)
    emitted = set()

    def _build_omit_type(inner, wchildren, parent_instance_type):
        """Build an Omit<Base, 'field1' | 'field2'> & { ... } type string."""
        omit_fields = ' | '.join(f"'{f}'" for f in sorted(wchildren.keys()))
        parts = []
        nodes = state_model.index.get(parent_instance_type, [])
        node = nodes[0]
        for field_name, child_it in sorted(wchildren.items()):
            _, child_wtype, child_ptype, child_ops = writable_info[child_it]
            child_node = node.children[field_name]
            if child_node.many and Operation.CREATE in child_ops:
                parts.append(f'{field_name}: Creatable<{child_wtype}, {child_ptype}>')
            elif child_node.many:
                parts.append(f'{field_name}: {child_wtype}[]')
            else:
                parts.append(f'{field_name}: {child_wtype}')
        fields_str = '; '.join(parts)
        return f'Omit<{inner}, {omit_fields}> & {{ {fields_str} }}'

    def emit_writable_type(instance_type):
        if instance_type in emitted or instance_type == anchor_type:
            return
        # Emit children first
        for child_it in writable_children.get(instance_type, {}).values():
            emit_writable_type(child_it)

        iface_name, wtype_name, ptype_name, ops = writable_info[instance_type]
        wchildren = writable_children.get(instance_type, {})

        # Build the base type with Saveable/Deleteable wrappers
        inner = iface_name
        if Operation.DELETE in ops:
            inner = f'Deleteable<{inner}>'
        if Operation.SAVE in ops:
            inner = f'Saveable<{inner}, {ptype_name}>'

        if wchildren:
            rhs = _build_omit_type(inner, wchildren, instance_type)
            type_lines.append(f'type {wtype_name} = {rhs};')
        else:
            type_lines.append(f'type {wtype_name} = {inner};')

        emitted.add(instance_type)

    for instance_type in writable_info:
        emit_writable_type(instance_type)

    # Now build the channel-specific state type.
    anchor_iface = interface_name(state_model.nested_serializer.__class__)
    anchor_node = state_model

    # Check if the anchor itself is writable
    anchor_writable = anchor_type in writable_info

    # Collect relation fields on the anchor that point to writable children
    anchor_writable_fields = {}
    for field_name, child_node in anchor_node.children.items():
        if child_node.instance_type in writable_info:
            anchor_writable_fields[field_name] = child_node

    if not anchor_writable_fields and not anchor_writable:
        return type_lines, None

    # Build the state type name from the anchor serializer
    channel_name = state_model.nested_serializer.__class__.__name__
    channel_name = channel_name.replace('Serializer', '')
    state_type_name = f'{channel_name}State'

    if anchor_writable:
        _, _, anchor_ptype, anchor_ops = writable_info[anchor_type]
        base = anchor_iface
        if Operation.DELETE in anchor_ops:
            base = f'Deleteable<{base}>'
        if Operation.SAVE in anchor_ops:
            base = f'Saveable<{base}, {anchor_ptype}>'
    else:
        base = anchor_iface

    if anchor_writable_fields:
        rhs = _build_omit_type(base, {
            f: child.instance_type
            for f, child in anchor_writable_fields.items()
        }, anchor_type)
        type_lines.append(f'type {state_type_name} = {rhs};')
    elif anchor_writable:
        type_lines.append(f'type {state_type_name} = {base};')
    else:
        return type_lines, None

    return type_lines, state_type_name


def generate_ts_class(context_channel_class, urlpattern, import_types):
    """Generate TypeScript class code for a ContextChannel.

    Returns (code_lines, writable_used) where writable_used is True if
    Saveable/Deleteable/Creatable helper types are needed in imports.
    """
    # First, we get the endpoint pattern and the parameters from our previous function
    endpoint, parameters = pattern_to_ts(urlpattern)

    name = context_channel_class.__name__
    anchor = context_channel_class.Meta.state
    if context_channel_class.many:
        many = 'true'
        anchor = anchor.child
    else:
        many = 'false'

    anchor_module = anchor.__class__.__module__
    anchor_name = anchor.__class__.__name__
    app = anchor_module.split('.')[0]
    base_state_type = interface_name(anchor.__class__)

    import_types[app].append(anchor.__class__)

    # Build channel-specific writable types
    writable = getattr(context_channel_class, '_writable', {})
    writable_type_lines, writable_state_type = _build_writable_types(
        context_channel_class._state_model, writable, import_types,
    )
    writable_used = bool(writable_type_lines)

    if writable_state_type:
        state_type = writable_state_type
    else:
        state_type = base_state_type

    if context_channel_class.many:
        state_type += '[]'

    if getattr(context_channel_class, 'RuntimeState', False):
        runtime_type = f'{context_channel_class.__name__}RuntimeState'
        types = f'{state_type}, {runtime_type}'
    else:
        runtime_type = None
        types = state_type

    # Writable type aliases go before the class
    code = []
    if writable_type_lines:
        code.extend(writable_type_lines)
        code.append('')

    # Base of the class
    code.extend([
        f"export class {name} extends ContextChannel<{types}> {{\n",
        f"  anchor = '{anchor_module}.{anchor_name}';",
        f"  endpoint: string = '{endpoint}';",
        "  args: { [key: string]: number | string } = {};",
        f"  many = {many};",
    ])

    # Add private properties based on parameters
    # for key, ts_type in parameters.items():
    #     code.append(f"  {key}: {ts_type};")

    if runtime_type:
        code.append(f'  runtimeState: {runtime_type} | undefined')
        types = typing.get_type_hints(context_channel_class.RuntimeState)

        code = [
            f"export interface {runtime_type} {{"
        ] + [
            f"  {var}: {TYPEMAP[_type]};"
            for var, _type in types.items()
        ] + [
            "}\n"
        ] + code
    else:
        code.append('  runtimeState = null;')

    code.append('  baseURL: string = SOCKET_URL;\n')

    # Constructor
    params = ', '.join([f"{key}: {ts_type}"
                        for key, ts_type in parameters.items()])

    params = f'{params}, token: string' if params else 'token: string'

    code.append(f"  constructor({params}) {{")
    code.append("    super(token);")
    code.extend([f"    this.args['{key}'] = {key};" for key in parameters])
    code.append("  }")

    code.append('')

    # Actions
    for action in list_actions(context_channel_class):
        hints = typing.get_type_hints(action)

        camel_action = snake_to_camel(action.__name__)

        return_type = hints.pop('return', type(None))
        return_type = get_ts_type(return_type)

        params = [f'{k}: {get_ts_type(v)}' for k, v in hints.items()]
        params = ', '.join(params)

        call_params = ', '.join(list(hints.keys()))

        code.append(f"  public async {camel_action}({params}): Promise<{return_type}> {{")
        code.append(f"    return await this.callAction('{action.__name__}', [{call_params}]);")
        code.append("  }")

        code.append('')

    model = context_channel_class._state_model.frontend_model()
    model_code = json.dumps(model, indent=2)
    indented = "\n".join("  " + line for line in model_code.splitlines())
    code.append(f'  model = {indented[2:]};')

    if writable:
        # Convert Operation members to strings for TypeScript output
        ts_writable = {
            itype: [op.name.lower() for op in ops]
            for itype, ops in writable.items()
        }
        writable_code = json.dumps(ts_writable, indent=2)
        writable_indented = "\n".join("  " + line for line in writable_code.splitlines())
        code.append(f'  writable = {writable_indented[2:]} as const;')

    code.append("}")

    return code, writable_used
