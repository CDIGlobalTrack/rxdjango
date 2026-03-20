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
    helper_imports = set()

    for urlpattern in consumer_urlpatterns:
        consumer_class = urlpattern.callback.consumer_class
        context_channel_class = consumer_class.context_channel_class
        class_code, class_helper_imports = generate_ts_class(
            context_channel_class, urlpattern, import_types,
        )
        helper_imports.update(class_helper_imports)
        body.append('\n')
        body.extend(class_code)

    if not body:
        return

    rxdjango_imports = ['ContextChannel', *sorted(helper_imports)]
    code.extend([
        f"import {{ {', '.join(rxdjango_imports)} }} from '@rxdjango/react';\n",
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

    Returns (type_lines, state_type_name_or_none, helper_imports) where
    type_lines is a list of TypeScript type alias lines to emit before the
    class, state_type_name_or_none is the channel-specific state type name if
    any writable types exist, or None if the channel is fully read-only, and
    helper_imports is the subset of Saveable/Deleteable/Creatable actually
    referenced by the generated types.
    """
    if not writable:
        return [], None, set()

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
        return [], None, set()

    type_lines = []
    helper_imports = set()

    # Emit payload types first — one per writable serializer
    for instance_type in writable_info:
        nodes = state_model.index.get(instance_type, [])
        if not nodes:
            continue
        _, _, ptype_name, _ = writable_info[instance_type]
        type_lines.extend(_generate_payload_lines(ptype_name, nodes[0]))
        type_lines.append('')

    anchor_type = state_model.instance_type
    emitted = set()

    descendants_with_writable = {}

    def has_writable_descendant(node):
        cached = descendants_with_writable.get(node.instance_type)
        if cached is not None:
            return cached

        result = node.instance_type in writable_info or any(
            has_writable_descendant(child)
            for child in node.children.values()
        )
        descendants_with_writable[node.instance_type] = result
        return result

    def ensure_import(node):
        serializer_class = node.nested_serializer.__class__
        app = serializer_class.__module__.split('.')[0]
        import_types[app].append(serializer_class)

    def wrap_base_type(node):
        ensure_import(node)
        iface_name = interface_name(node.nested_serializer.__class__)
        info = writable_info.get(node.instance_type)
        if not info:
            return iface_name

        _, _, ptype_name, ops = info
        inner = iface_name
        if Operation.DELETE in ops:
            helper_imports.add('Deleteable')
            inner = f'Deleteable<{inner}>'
        if Operation.SAVE in ops:
            helper_imports.add('Saveable')
            inner = f'Saveable<{inner}, {ptype_name}>'
        return inner

    def build_field_type(child_node):
        child_info = writable_info.get(child_node.instance_type)
        if child_info:
            _, child_wtype, child_ptype, child_ops = child_info
            if child_node.many and Operation.CREATE in child_ops:
                helper_imports.add('Creatable')
                return f'Creatable<{child_wtype}, {child_ptype}>'
            if child_node.many:
                return f'{child_wtype}[]'
            return child_wtype

        child_expr = build_inline_expr(child_node)
        if child_node.many:
            return f'({child_expr})[]'
        return child_expr

    def render_type_expr(node, indent_level=0):
        indent = '  ' * indent_level
        base = wrap_base_type(node)
        writable_fields = []
        for field_name, child_node in sorted(node.children.items()):
            if not has_writable_descendant(child_node):
                continue
            writable_fields.append((field_name, child_node))

        if not writable_fields:
            return [f'{indent}{base}']

        lines = [f'{indent}Omit<{base},']
        omit_fields = [
            f"{'  ' * (indent_level + 1)}'{field_name}'"
            for field_name, _ in writable_fields
        ]
        if len(omit_fields) == 1:
            lines.append(omit_fields[0])
        else:
            for omit_field in omit_fields[:-1]:
                lines.append(f'{omit_field} |')
            lines.append(omit_fields[-1])
        lines.append(f'{indent}> & {{')

        for field_name, child_node in writable_fields:
            field_prefix = f"{'  ' * (indent_level + 1)}{field_name}: "
            field_type_lines = render_field_type(child_node, indent_level + 2)
            if len(field_type_lines) == 1:
                lines.append(f"{field_prefix}{field_type_lines[0].strip()};")
                continue

            lines.append(f"{field_prefix}{field_type_lines[0].strip()}")
            lines.extend(field_type_lines[1:])
            lines[-1] = f'{lines[-1]};'

        lines.append(f'{indent}}}')
        return lines

    def render_field_type(child_node, indent_level=0):
        indent = '  ' * indent_level
        child_info = writable_info.get(child_node.instance_type)
        if child_info:
            _, child_wtype, child_ptype, child_ops = child_info
            if child_node.many and Operation.CREATE in child_ops:
                helper_imports.add('Creatable')
                return [f'{indent}Creatable<{child_wtype}, {child_ptype}>']
            if child_node.many:
                return [f'{indent}{child_wtype}[]']
            return [f'{indent}{child_wtype}']

        child_expr_lines = render_type_expr(child_node, indent_level)
        if not child_node.many:
            return child_expr_lines

        if len(child_expr_lines) == 1:
            return [f"{indent}({child_expr_lines[0].strip()})[]"]

        return [
            f'{indent}(',
            *child_expr_lines,
            f'{indent})[]',
        ]

    def build_inline_expr(node):
        base = wrap_base_type(node)
        writable_fields = []
        for field_name, child_node in sorted(node.children.items()):
            if not has_writable_descendant(child_node):
                continue
            writable_fields.append((field_name, build_field_type(child_node)))

        if not writable_fields:
            return base

        omit_fields = ' | '.join(f"'{field_name}'" for field_name, _ in writable_fields)
        fields_str = '; '.join(
            f'{field_name}: {field_type}'
            for field_name, field_type in writable_fields
        )
        return f'Omit<{base}, {omit_fields}> & {{ {fields_str} }}'

    def emit_writable_types(node):
        for child_node in node.children.values():
            if has_writable_descendant(child_node):
                emit_writable_types(child_node)

        instance_type = node.instance_type
        if instance_type == anchor_type or instance_type in emitted:
            return
        if instance_type not in writable_info:
            return

        _, wtype_name, _, _ = writable_info[instance_type]
        expr_lines = render_type_expr(node)
        type_lines.append(f'type {wtype_name} = {expr_lines[0].strip()}')
        type_lines.extend(expr_lines[1:])
        type_lines[-1] = f'{type_lines[-1]};'
        emitted.add(instance_type)

    if not has_writable_descendant(state_model):
        return type_lines, None, helper_imports

    emit_writable_types(state_model)

    # Build the state type name from the anchor serializer
    channel_name = state_model.nested_serializer.__class__.__name__
    channel_name = channel_name.replace('Serializer', '')
    state_type_name = f'{channel_name}State'
    expr_lines = render_type_expr(state_model)
    type_lines.append(f'type {state_type_name} = {expr_lines[0].strip()}')
    type_lines.extend(expr_lines[1:])
    type_lines[-1] = f'{type_lines[-1]};'

    return type_lines, state_type_name, helper_imports


def generate_ts_class(context_channel_class, urlpattern, import_types):
    """Generate TypeScript class code for a ContextChannel.

    Returns (code_lines, helper_imports) where helper_imports is the subset of
    Saveable/Deleteable/Creatable needed in imports.
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
    writable_type_lines, writable_state_type, helper_imports = _build_writable_types(
        context_channel_class._state_model, writable, import_types,
    )

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

    return code, helper_imports
