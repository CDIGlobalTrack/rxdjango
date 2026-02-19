"""RxDjango exception classes.

Custom exceptions used throughout the RxDjango framework for signaling
specific error conditions in channel authentication, state management,
and action execution.
"""


class UnknownProperty(Exception):
    """Raised when a serializer references a model property that does not exist.

    This typically indicates a misconfigured serializer field or a missing
    ``@related_property`` decorator on a custom model property.
    """
    pass


class AnchorDoesNotExist(Exception):
    """Raised when the requested anchor object cannot be found.

    Occurs during WebSocket connection when the anchor ID provided in the
    URL route does not correspond to an existing database record.
    """
    pass


class UnauthorizedError(Exception):
    """Raised when authentication fails due to a missing or invalid token.

    The WebSocket connection is rejected with a 401-equivalent status when
    no token is provided or the token does not match any active
    ``rest_framework.authtoken.models.Token``.
    """
    pass


class ForbiddenError(Exception):
    """Raised when an authenticated user lacks permission to access a channel.

    Occurs when ``has_permission()`` on the ContextChannel returns ``False``
    for the authenticated user, resulting in a 403-equivalent rejection.
    """
    pass


class RxDjangoBug(Exception):
    """Raised when an internal invariant is violated.

    Indicates a bug in RxDjango itself rather than a user configuration error.
    If encountered, this should be reported as an issue.
    """
    pass


class ActionNotAsync(Exception):
    """Raised when an ``@action``-decorated method is not an async function.

    All action methods on ContextChannel subclasses must be defined with
    ``async def``. This exception is raised during channel metaclass
    initialization if a synchronous method is decorated with ``@action``.
    """
    pass
