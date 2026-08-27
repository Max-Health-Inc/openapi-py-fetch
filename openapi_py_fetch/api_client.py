"""API client module — makes real HTTP requests via httpx."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx

from .configuration import Configuration
from .exceptions import ApiException

logger = logging.getLogger(__name__)


class ApiClient:
    """Generic API client for OpenAPI client library builds.

    Handles configuration, authentication, and HTTP communication via httpx.
    Generated API classes delegate all HTTP work to this client.
    """

    def __init__(
        self,
        configuration: Configuration | None = None,
        header_name: str | None = None,
        header_value: str | None = None,
        cookie: str | None = None,
    ) -> None:
        if configuration is None:
            configuration = Configuration.get_default()
        self.configuration = configuration
        self.default_headers: dict[str, str] = {}
        if header_name and header_value:
            self.default_headers[header_name] = header_value
        self.cookie = cookie

    # ------------------------------------------------------------------
    # Core HTTP transport
    # ------------------------------------------------------------------

    def call_api(
        self,
        resource_path: str,
        method: str,
        path_params: dict[str, Any] | None = None,
        query_params: dict[str, Any] | None = None,
        header_params: dict[str, str] | None = None,
        body: Any = None,
        _return_http_info: bool = False,
    ) -> Any:
        """Make a real HTTP request and return parsed JSON (or raw text)."""
        # Substitute path parameters
        url = resource_path
        for key, value in (path_params or {}).items():
            url = re.sub(r"\{" + re.escape(key) + r"\}", str(value), url)

        # Build full URL
        host = (self.configuration.host or "http://localhost").rstrip("/")
        full_url = host + url

        # Headers
        headers: dict[str, str] = {**self.default_headers}
        headers["Accept"] = "application/json"
        if header_params:
            headers.update(header_params)

        # Authentication
        if self.configuration.access_token:
            headers["Authorization"] = f"Bearer {self.configuration.access_token}"

        if self.configuration.api_key:
            for key, value in self.configuration.api_key.items():
                prefix = self.configuration.api_key_prefix.get(key, "")
                if prefix:
                    headers[key] = f"{prefix} {value}"
                else:
                    # api_key can go in header or query — default to query
                    if query_params is None:
                        query_params = {}
                    query_params[key] = value

        # Cookie
        cookies: dict[str, str] = {}
        if self.cookie:
            cookies["cookie"] = self.cookie

        # Prepare body
        json_body = None
        content = None
        if body is not None:
            if isinstance(body, str):
                try:
                    json_body = json.loads(body)
                except (json.JSONDecodeError, TypeError):
                    content = body
                    headers["Content-Type"] = "text/plain"
            elif isinstance(body, (dict, list)):
                json_body = body
            else:
                json_body = body

        # Execute request
        timeout = httpx.Timeout(30.0, connect=10.0)
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                response = client.request(
                    method=method,
                    url=full_url,
                    params=query_params or None,
                    headers=headers,
                    json=json_body,
                    content=content,
                    cookies=cookies or None,
                )
        except httpx.ConnectError as exc:
            raise ApiException(status=0, reason=f"Connection error: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise ApiException(status=0, reason=f"Request timed out: {exc}") from exc

        # Handle errors
        if response.status_code >= 400:
            raise ApiException(
                status=response.status_code,
                reason=response.reason_phrase or "Error",
                body=response.text,
                headers=dict(response.headers),
            )

        # Parse response
        data = None
        if response.content:
            content_type = response.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    data = response.json()
                except (json.JSONDecodeError, ValueError):
                    data = response.text
            else:
                data = response.text

        if _return_http_info:
            return data, response.status_code, dict(response.headers)
        return data

    # ------------------------------------------------------------------
    # Header helpers
    # ------------------------------------------------------------------

    def select_header_accept(self, accepts: list[str]) -> str | None:
        """Return Accept header based on an array of accepts provided."""
        if not accepts:
            return None
        for accept in accepts:
            if "application/json" in accept:
                return accept
        return accepts[0]

    def select_header_content_type(self, content_types: list[str]) -> str:
        """Return Content-Type header based on an array of content types."""
        if not content_types:
            return "application/json"
        for ct in content_types:
            if "application/json" in ct:
                return ct
        return content_types[0]
