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
    __actions.add(method)
    return method


def list_actions(channel):
    """List all decorated methods in this django deployment"""
    for method in channel.__dict__.values():
        if method in __actions:
            yield method


async def execute_action(channel, method_name, params):
    method = getattr(channel, method_name, None)
    if not method:
        raise ForbiddenError
    if not method in __actions:
        raise ForbiddenError

    return await method(*params)
