"""Exception classes for OpenAPI client."""

from __future__ import annotations


class OpenApiException(Exception):
    """Base exception for OpenAPI client."""


class ApiTypeError(OpenApiException, TypeError):
    """API type error."""

    def __init__(
        self,
        msg: str,
        path_to_item: list | None = None,
        valid_classes: tuple | None = None,
        key_type: bool | None = None,
    ) -> None:
        self.path_to_item = path_to_item
        self.valid_classes = valid_classes
        self.key_type = key_type
        super().__init__(msg)


class ApiValueError(OpenApiException, ValueError):
    """API value error."""

    def __init__(self, msg: str, path_to_item: list | None = None) -> None:
        self.path_to_item = path_to_item
        super().__init__(msg)


class ApiKeyError(OpenApiException, KeyError):
    """API key error."""

    def __init__(self, msg: str, path_to_item: list | None = None) -> None:
        self.path_to_item = path_to_item
        super().__init__(msg)


class ApiAttributeError(OpenApiException, AttributeError):
    """API attribute error."""

    def __init__(self, msg: str, path_to_item: list | None = None) -> None:
        self.path_to_item = path_to_item
        super().__init__(msg)


class ApiException(OpenApiException):
    """API exception with HTTP status information.

    Attributes:
        status: HTTP status code
        reason: Error reason text
        body: Response body
        headers: Response headers
    """

    def __init__(
        self,
        status: int = 0,
        reason: str = "",
        body: str | None = None,
        headers: dict | None = None,
    ) -> None:
        self.status = status
        self.reason = reason
        self.body = body
        self.headers = headers or {}
        super().__init__(f"({status}) Reason: {reason}")
