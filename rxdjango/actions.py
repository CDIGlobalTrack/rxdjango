import asyncio
import typing
import inspect
from collections import defaultdict
from .exceptions import ForbiddenError, ActionNotAsync

__actions = set()


def action(method):
    """Methods in a ContextChannel decorated with @action will be
    implemented in the frontend counterpart ContextChannel class,
    so that the method can be called directly from the frontend.
    """
    if not asyncio.iscoroutinefunction(method):
        raise ActionNotAsync(f'@action decorator requires "{method.__name__}" to be async')
    wrapped = method
    while getattr(wrapped, '__wrapped__', None):
        wrapped = wrapped.__wrapped__
    __actions.add(wrapped)
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
    return await method(*params)


def _verify_method(method):
    if not method:
        raise ForbiddenError
    if getattr(method, '__func__', None):
        method = method.__func__
    while getattr(method, '__wrapped__', None):
        method = method.__wrapped__
    if not method in __actions:
        raise ForbiddenError

