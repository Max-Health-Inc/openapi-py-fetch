"""
openapi-py-fetch — Lightweight Python OpenAPI 3.x client runtime and generator.

Zero Java, zero npm — just Python 3.11+ and your OpenAPI spec.

Runtime:
    from openapi_py_fetch import ApiClient, Configuration, ApiException

Generator:
    from openapi_py_fetch.generator import generate_client_package
"""

from __future__ import annotations

__version__ = "0.2.0"

# Runtime public API
from .api_client import ApiClient
from .api_response import ApiResponse
from .configuration import Configuration
from .exceptions import (
    ApiAttributeError,
    ApiException,
    ApiKeyError,
    ApiTypeError,
    ApiValueError,
    OpenApiException,
)

__all__ = [
    "ApiClient",
    "ApiResponse",
    "Configuration",
    "ApiAttributeError",
    "ApiException",
    "ApiKeyError",
    "ApiTypeError",
    "ApiValueError",
    "OpenApiException",
]
