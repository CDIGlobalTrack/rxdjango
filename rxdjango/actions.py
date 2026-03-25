import asyncio
import inspect
import types
import typing
from datetime import datetime
from .exceptions import ForbiddenError, ActionNotAsync

# A set of references for registered actions, so to protect non-action methods
# from being called by frontend
__actions = set()


class ActionValidationError(Exception):
    code = 400


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
    _validate_action_params(method, params)
    return await method(*params)


def _validate_action_params(method, params):
    signature = inspect.signature(method)
    parameters = [
        parameter for parameter in signature.parameters.values()
        if parameter.name != 'self'
    ]
    positional = [
        parameter for parameter in parameters
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    required = [
        parameter for parameter in positional
        if parameter.default is inspect.Parameter.empty
    ]
    accepts_varargs = any(
        parameter.kind is inspect.Parameter.VAR_POSITIONAL
        for parameter in parameters
    )

    if len(params) < len(required):
        raise ActionValidationError(
            f'Expected at least {len(required)} params, got {len(params)}'
        )

    if not accepts_varargs and len(params) > len(positional):
        raise ActionValidationError(
            f'Expected at most {len(positional)} params, got {len(params)}'
        )

    type_hints = typing.get_type_hints(method)
    type_hints.pop('return', None)

    for index, parameter in enumerate(positional):
        if index >= len(params):
            break
        params[index] = _coerce_action_param(
            params[index],
            type_hints.get(parameter.name),
            parameter.name,
        )


def _coerce_action_param(value, expected_type, parameter_name):
    if expected_type is None or expected_type is typing.Any:
        return value

    if expected_type is datetime:
        if not isinstance(value, str):
            raise ActionValidationError(
                f'Param "{parameter_name}" must be an ISO datetime string'
            )
        try:
            return datetime.fromisoformat(value)
        except ValueError as exc:
            raise ActionValidationError(
                f'Param "{parameter_name}" must be an ISO datetime string'
            ) from exc

    if _matches_type(value, expected_type):
        return value

    raise ActionValidationError(
        f'Param "{parameter_name}" must be of type {_type_name(expected_type)}'
    )


def _matches_type(value, expected_type):
    origin = typing.get_origin(expected_type)
    if origin is None:
        try:
            return isinstance(value, expected_type)
        except TypeError:
            return True

    if origin in (typing.Union, types.UnionType):
        return any(_matches_type(value, option) for option in typing.get_args(expected_type))

    try:
        return isinstance(value, origin)
    except TypeError:
        return True


def _type_name(expected_type):
    origin = typing.get_origin(expected_type)
    if origin is None:
        return getattr(expected_type, '__name__', str(expected_type))

    args = typing.get_args(expected_type)
    if not args:
        return getattr(origin, '__name__', str(origin))

    inner = ', '.join(_type_name(arg) for arg in args)
    return f'{getattr(origin, "__name__", str(origin))}[{inner}]'


def _verify_method(method):
    """Checks that a method is registered as an action"""
    if not method:
        raise ForbiddenError
    if getattr(method, '__func__', None):
        method = method.__func__
    while getattr(method, '__wrapped__', None):
        method = method.__wrapped__
    if method not in __actions:
        raise ForbiddenError
