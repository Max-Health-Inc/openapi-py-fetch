"""API response wrapper."""

from __future__ import annotations

from typing import Any


class ApiResponse:
    """API response wrapper.

    Attributes:
        status_code: HTTP status code
        headers: HTTP response headers
        data: Deserialized response data
        raw_data: Raw response body bytes
    """

    def __init__(
        self,
        status_code: int = 0,
        headers: dict | None = None,
        data: Any = None,
        raw_data: bytes | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.data = data
        self.raw_data = raw_data
