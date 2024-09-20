"""Error classes for the Passes API wrapper."""

from typing import Optional


class InvalidURLError(Exception):
    """An exception raised when an invalid URL is provided."""

    def __init__(self, message: Optional[str] = None) -> None:
        super().__init__(message or "An invalid URL was provided.")


class AuthorizationError(Exception):
    """An exception raised when an authorization error occurs."""

    def __init__(self, message: Optional[str] = None) -> None:
        super().__init__(message or "An authorization error occurred.")
