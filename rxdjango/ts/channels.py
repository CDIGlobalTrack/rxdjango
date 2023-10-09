import os
import re
import json
from collections import defaultdict
from channels.routing import ProtocolTypeRouter, URLRouter
from django.urls import URLPattern, URLResolver
from django.conf import settings
from rxdjango.consumers import StateConsumer
from . import header
from . import interface_name

def create_app_channels(app):
    consumer_urlpatterns = list_consumer_patterns(app)

    if not consumer_urlpatterns:
        return

    path = os.path.join(settings.RX_FRONTEND_DIR, f'{app}/{app}.channels.ts')

    code = header(
        app,
        f'Based on all StateChannel.as_asgi() calls in {settings.ASGI_APPLICATION}',
        f'This is expected to match {app}/channels.py',
    )

    code.extend([
        f"import {{ StateChannel }} from '@rxdjango/react';\n",
        f'const SOCKET_URL = {settings.RX_WEBSOCKET_URL};',
    ])

    import_types = defaultdict(list)
    body = []

    for urlpattern in consumer_urlpatterns:
        consumer_class = urlpattern.callback.consumer_class
        state_channel_class = consumer_class.state_channel_class
        class_name = state_channel_class.__name__
        class_code = generate_ts_class(state_channel_class, urlpattern, import_types)
        body.append('\n')
        body.extend(class_code)

    if not body:
        return

    code.append('')  # line break
    code.extend(build_imports(import_types, app))
    code.extend(body)

    content = '\n'.join(code)

    try:
        fh = open(path, 'w')
    except FileNotFoundError:
        os.mkdir(os.path.dirname(path))
        fh = open(path, 'w')
    fh.write(content)
    fh.close()


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

            state_channel_class =  callback.consumer_class.state_channel_class

            if state_channel_class.__module__.startswith(f'{app_name}.'):
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
        interfaces = ', '.join(interfaces)
        path = '.' if app == this_app else f'../{app}'
        code.append(f"import {{ {interfaces} }} from '{path}/{app}.interfaces.d';")

    return code

def generate_ts_class(state_channel_class, urlpattern, import_types):
    # First, we get the endpoint pattern and the parameters from our previous function
    endpoint, parameters = pattern_to_ts(urlpattern)

    name = state_channel_class.__name__
    anchor = state_channel_class.Meta.anchor

    anchor_module = anchor.__class__.__module__
    anchor_name = anchor.__class__.__name__
    app = anchor_module.split('.')[0]
    state_type = interface_name(anchor.__class__)

    import_types[app].append(anchor.__class__)

    if getattr(anchor, 'many', False):
        state_type += '[]'

    # Base of the class
    code = [
        f"export class {name} extends StateChannel<{state_type}> {{\n",
        f"  anchor = '{anchor_module}.{anchor_name}';",
        f"  endpoint: string = '{endpoint}';\n",
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

    model = state_channel_class._state_model.frontend_model()
    model_code = json.dumps(model, indent=2)
    indented = "\n".join("  " + line for line in model_code.splitlines())
    code.append(f'  model = {indented[2:]};')

    code.append(f"}}")

    return code
