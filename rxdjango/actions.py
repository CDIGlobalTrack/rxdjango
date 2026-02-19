import asyncio
import typing
import inspect
from datetime import datetime
from collections import defaultdict
from .exceptions import ForbiddenError, ActionNotAsync

# A set of references for registered actions, so to protect non-action methods
# from being called by frontend
__actions = set()


def action(method):
    """Decorator to expose a ContextChannel method as a frontend-callable RPC action.

    Actions are automatically discovered and exported to the generated TypeScript
    channel class. The method must be async.

    The decorated method's type hints are inspected to auto-convert parameters
    (e.g. ``datetime`` strings are converted to ``datetime`` objects).

    Example::

        @action
        async def update_status(self, status: str) -> dict:
            # Called from frontend: await channel.updateStatus("active")
            self.instance.status = status
            self.instance.save()
            return {"success": True}
    """
    if not asyncio.iscoroutinefunction(method):
        raise ActionNotAsync(f'@action decorator requires "{method.__name__}" to be async')
    wrapped = method
    # Method may be decorated, find the original method ref
    while getattr(wrapped, '__wrapped__', None):
        wrapped = wrapped.__wrapped__
    # Register method to be callable
    __actions.add(wrapped)
    # Inspect method parameters so to make type conversions
    # when calling method
    hints = typing.get_type_hints(method)
    hints.pop('return', None)
    hints = list(hints.values())
    method.__datetime_fields = []
    for i in range(len(hints)):
        if hints[i] is datetime:
            method.__datetime_fields.append(i)
    # Return the original method
    return method


def list_actions(channel):
    """List all decorated methods in this django deployment"""
    for method in channel.__dict__.values():
        try:
            if method in __actions:
                yield method
        except TypeError:
            pass


async def execute_action(channel, method_name, params):
    method = getattr(channel, method_name, None)
    _verify_method(method)
    for i in method.__datetime_fields:
        params[i] = datetime.fromisoformat(params[i])
    return await method(*params)


def _verify_method(method):
    """Checks that a method is registered as an action"""
    if not method:
        raise ForbiddenError
    if getattr(method, '__func__', None):
        method = method.__func__
    while getattr(method, '__wrapped__', None):
        method = method.__wrapped__
    if not method in __actions:
        raise ForbiddenError
