import os
import re
import json
import typing
from collections import defaultdict
from channels.routing import ProtocolTypeRouter, URLRouter
from django.urls import URLPattern, URLResolver
from django.conf import settings
from rxdjango.consumers import StateConsumer
from rxdjango.actions import list_actions
from . import header, interface_name, diff, get_ts_type, snake_to_camel

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

    code.extend([
        f"import {{ ContextChannel }} from '@rxdjango/react';\n",
        f'const SOCKET_URL = {settings.RX_WEBSOCKET_URL};',
    ])

    import_types = defaultdict(list)
    body = []

    for urlpattern in consumer_urlpatterns:
        consumer_class = urlpattern.callback.consumer_class
        context_channel_class = consumer_class.context_channel_class
        class_name = context_channel_class.__name__
        class_code = generate_ts_class(context_channel_class, urlpattern, import_types)
        body.append('\n')
        body.extend(class_code)

    if not body:
        return

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

            context_channel_class =  callback.consumer_class.context_channel_class

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
        interfaces = [ interface_name(seri) for seri in app_serializers ]
        interfaces = ', '.join(sorted(interfaces))
        path = '.' if app == this_app else f'../{app}'
        code.append(f"import {{ {interfaces} }} from '{path}/{app}.interfaces.d';")

    return code

def generate_ts_class(context_channel_class, urlpattern, import_types):
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
    state_type = interface_name(anchor.__class__)

    import_types[app].append(anchor.__class__)

    if getattr(anchor, 'many', False):
        state_type += '[]'

    # Base of the class
    code = [
        f"export class {name} extends ContextChannel<{state_type}> {{\n",
        f"  anchor = '{anchor_module}.{anchor_name}';",
        f"  endpoint: string = '{endpoint}';\n",
        f"  args: {{ [key: string]: number | string }} = {{}};\n",
        f"  many = {many};\n",
    ]

    # Add private properties based on parameters
    # for key, ts_type in parameters.items():
    #     code.append(f"  {key}: {ts_type};")

    code.append(f'  baseURL: string = SOCKET_URL;\n')

    # Constructor
    params = ', '.join([f"{key}: {ts_type}"
                        for key, ts_type in parameters.items()])

    params = f'{params}, token: string' if params else 'token: string'

    code.append(f"  constructor({params}) {{")
    code.append(f"    super(token);")
    code.extend([f"    this.args['{key}'] = {key};" for key in parameters])
    code.append(f"  }}")

    code.append('')

    # Actions
    for action in list_actions(context_channel_class):
        hints = typing.get_type_hints(action)

        camel_action = snake_to_camel(action.__name__)

        return_type = hints.pop('return', type(None))
        return_type = get_ts_type(return_type)

        params = [ f'{k}: {get_ts_type(v)}' for k, v in hints.items() ]
        params = ', '.join(params)

        call_params = ', '.join(list(hints.keys()))

        code.append(f"  public async {camel_action}({params}): {return_type} {{")
        code.append(f"    return await this.callAction('{action.__name__}', [{call_params}]);")
        code.append(f"  }}")

        code.append('')

    model = context_channel_class._state_model.frontend_model()
    model_code = json.dumps(model, indent=2)
    indented = "\n".join("  " + line for line in model_code.splitlines())
    code.append(f'  model = {indented[2:]};')

    code.append(f"}}")

    return code
