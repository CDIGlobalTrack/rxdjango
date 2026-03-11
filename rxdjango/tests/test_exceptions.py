"""
Tests for rxdjango.exceptions module.

Tests that custom exception classes are properly defined and behave as expected.
"""
import pytest

from rxdjango.exceptions import (
    UnknownProperty,
    AnchorDoesNotExist,
    UnauthorizedError,
    ForbiddenError,
    RxDjangoBug,
    ActionNotAsync,
)


class TestExceptions:
    """Tests for all custom exception classes."""

    @pytest.mark.parametrize("exc_class,message", [
        (UnknownProperty, "property 'foo' not found"),
        (AnchorDoesNotExist, "anchor 42 not found"),
        (UnauthorizedError, "error/unauthorized"),
        (ForbiddenError, "error/forbidden"),
        (RxDjangoBug, "invariant violation"),
        (ActionNotAsync, "@action requires async"),
    ])
    def test_exception_with_message(self, exc_class, message):
        """Each exception should accept and store a message."""
        exc = exc_class(message)
        assert str(exc) == message

    @pytest.mark.parametrize("exc_class", [
        UnknownProperty,
        AnchorDoesNotExist,
        UnauthorizedError,
        ForbiddenError,
        RxDjangoBug,
        ActionNotAsync,
    ])
    def test_exception_inherits_from_exception(self, exc_class):
        """All custom exceptions should inherit from Exception."""
        assert issubclass(exc_class, Exception)

    @pytest.mark.parametrize("exc_class", [
        UnknownProperty,
        AnchorDoesNotExist,
        UnauthorizedError,
        ForbiddenError,
        RxDjangoBug,
        ActionNotAsync,
    ])
    def test_exception_can_be_raised_and_caught(self, exc_class):
        """Each exception should be raisable and catchable."""
        with pytest.raises(exc_class):
            raise exc_class("test")

    def test_exception_without_message(self):
        """Exceptions should work without a message."""
        exc = ForbiddenError()
        assert str(exc) == ''
