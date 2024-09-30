"""Error classes for the Passes API wrapper."""

from typing import Optional


class InvalidURLError(Exception):
    """An exception raised when an invalid URL is provided."""

    def __init__(self, url: str) -> None:
        super().__init__(f"The URL '{url}' is invalid.")


class AuthorizationError(Exception):
    """An exception raised when an authorization error occurs."""

    def __init__(self, message: Optional[str] = None) -> None:
        super().__init__(message or "An authorization error occurred.")


class UserNotFoundError(Exception):
    """An exception raised when a user is not found."""

    def __init__(self, username: str) -> None:
        super().__init__(f"The user '{username}' was not found.")


class ChannelNotFoundError(Exception):
    """An exception raised when a channel is not found."""

    def __init__(self, username: str) -> None:
        super().__init__(f"The message channel for user '{username}' was not found.")
