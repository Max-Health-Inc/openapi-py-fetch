"""Tests for openapi_py_fetch runtime: Configuration, ApiClient, exceptions, ApiResponse."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from openapi_py_fetch import (
    ApiAttributeError,
    ApiClient,
    ApiException,
    ApiKeyError,
    ApiResponse,
    ApiTypeError,
    ApiValueError,
    Configuration,
    OpenApiException,
)

# =========================================================================
# Configuration
# =========================================================================


class TestConfiguration:
    """Configuration class tests."""

    def test_defaults(self):
        cfg = Configuration()
        assert cfg.host == "http://localhost"
        assert cfg.api_key == {}
        assert cfg.api_key_prefix == {}
        assert cfg.access_token is None
        assert cfg.username is None
        assert cfg.password is None
        assert cfg.verify_ssl is True

    def test_custom_host(self):
        cfg = Configuration(host="https://api.example.com")
        assert cfg.host == "https://api.example.com"

    def test_api_key_config(self):
        cfg = Configuration(
            api_key={"api_key": "my-key"},
            api_key_prefix={"api_key": "Bearer"},
        )
        assert cfg.api_key["api_key"] == "my-key"
        assert cfg.api_key_prefix["api_key"] == "Bearer"

    def test_bearer_token(self):
        cfg = Configuration(access_token="tok-123")
        assert cfg.access_token == "tok-123"

    def test_basic_auth(self):
        cfg = Configuration(username="admin", password="secret")
        assert cfg.username == "admin"
        assert cfg.password == "secret"

    def test_singleton_default(self):
        # Reset
        Configuration._default = None
        d1 = Configuration.get_default()
        d2 = Configuration.get_default()
        assert d1 is d2

    def test_set_default(self):
        Configuration._default = None
        custom = Configuration(host="https://custom.api")
        Configuration.set_default(custom)
        assert Configuration.get_default() is custom
        Configuration._default = None  # cleanup

    def test_server_variables(self):
        cfg = Configuration(
            server_index=0,
            server_variables={"env": "prod"},
        )
        assert cfg.server_index == 0
        assert cfg.server_variables == {"env": "prod"}


# =========================================================================
# ApiClient
# =========================================================================


class TestApiClient:
    """ApiClient creation and header selection tests."""

    def test_default_creation(self):
        Configuration._default = None
        client = ApiClient()
        assert client.configuration is not None
        assert client.default_headers == {}
        assert client.cookie is None

    def test_custom_header(self):
        client = ApiClient(header_name="X-Custom", header_value="val")
        assert client.default_headers["X-Custom"] == "val"

    def test_cookie(self):
        client = ApiClient(cookie="session=abc")
        assert client.cookie == "session=abc"

    def test_select_header_accept_json(self):
        client = ApiClient()
        assert client.select_header_accept(["application/json", "text/plain"]) == "application/json"

    def test_select_header_accept_empty(self):
        client = ApiClient()
        assert client.select_header_accept([]) is None

    def test_select_header_accept_fallback(self):
        client = ApiClient()
        assert client.select_header_accept(["text/plain"]) == "text/plain"

    def test_select_header_content_type_json(self):
        client = ApiClient()
        assert client.select_header_content_type(["application/json"]) == "application/json"

    def test_select_header_content_type_default(self):
        client = ApiClient()
        assert client.select_header_content_type([]) == "application/json"


class TestApiClientHTTP:
    """Tests for actual HTTP calls via call_api using httpx mocking."""

    def _mock_response(self, status=200, json_data=None, text="", headers=None):
        """Create a mock httpx.Response."""
        resp_headers = {"content-type": "application/json"}
        if headers:
            resp_headers.update(headers)
        if json_data is not None:
            return httpx.Response(
                status_code=status,
                headers=resp_headers,
                json=json_data,
            )
        return httpx.Response(
            status_code=status,
            headers=resp_headers,
            text=text,
        )

    def test_get_request(self):
        cfg = Configuration(host="https://api.example.com")
        client = ApiClient(configuration=cfg)

        mock_resp = self._mock_response(json_data={"id": 1, "name": "Fido"})

        with patch.object(httpx.Client, "request", return_value=mock_resp):
            result = client.call_api("/pet/{petId}", "GET", path_params={"petId": 1})

        assert result == {"id": 1, "name": "Fido"}

    def test_post_with_body(self):
        cfg = Configuration(host="https://api.example.com")
        client = ApiClient(configuration=cfg)

        mock_resp = self._mock_response(json_data={"id": 2, "name": "Rex"})

        with patch.object(httpx.Client, "request", return_value=mock_resp) as mock_req:
            result = client.call_api(
                "/pet",
                "POST",
                body={"name": "Rex", "status": "available"},
            )

        assert result["name"] == "Rex"
        call_kwargs = mock_req.call_args
        assert call_kwargs.kwargs["json"] == {"name": "Rex", "status": "available"}

    def test_query_params(self):
        cfg = Configuration(host="https://api.example.com")
        client = ApiClient(configuration=cfg)

        mock_resp = self._mock_response(json_data=[])

        with patch.object(httpx.Client, "request", return_value=mock_resp) as mock_req:
            client.call_api(
                "/pet/findByStatus",
                "GET",
                query_params={"status": "available"},
            )

        assert mock_req.call_args.kwargs["params"] == {"status": "available"}

    def test_path_substitution(self):
        cfg = Configuration(host="https://api.example.com")
        client = ApiClient(configuration=cfg)

        mock_resp = self._mock_response(json_data={})

        with patch.object(httpx.Client, "request", return_value=mock_resp) as mock_req:
            client.call_api("/pet/{petId}", "GET", path_params={"petId": 42})

        assert mock_req.call_args.kwargs["url"] == "https://api.example.com/pet/42"

    def test_bearer_auth(self):
        cfg = Configuration(host="https://api.example.com", access_token="my-token")
        client = ApiClient(configuration=cfg)

        mock_resp = self._mock_response(json_data={})

        with patch.object(httpx.Client, "request", return_value=mock_resp) as mock_req:
            client.call_api("/secure", "GET")

        headers = mock_req.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer my-token"

    def test_api_key_in_query(self):
        cfg = Configuration(
            host="https://api.example.com",
            api_key={"api_key": "special-key"},
        )
        client = ApiClient(configuration=cfg)

        mock_resp = self._mock_response(json_data={})

        with patch.object(httpx.Client, "request", return_value=mock_resp) as mock_req:
            client.call_api("/store/inventory", "GET")

        assert mock_req.call_args.kwargs["params"]["api_key"] == "special-key"

    def test_api_key_with_prefix_in_header(self):
        cfg = Configuration(
            host="https://api.example.com",
            api_key={"Authorization": "xyz"},
            api_key_prefix={"Authorization": "ApiKey"},
        )
        client = ApiClient(configuration=cfg)

        mock_resp = self._mock_response(json_data={})

        with patch.object(httpx.Client, "request", return_value=mock_resp) as mock_req:
            client.call_api("/data", "GET")

        headers = mock_req.call_args.kwargs["headers"]
        assert headers["Authorization"] == "ApiKey xyz"

    def test_http_error_raises_exception(self):
        cfg = Configuration(host="https://api.example.com")
        client = ApiClient(configuration=cfg)

        mock_resp = httpx.Response(
            status_code=404,
            headers={"content-type": "application/json"},
            json={"message": "not found"},
        )

        with patch.object(httpx.Client, "request", return_value=mock_resp):
            with pytest.raises(ApiException) as exc_info:
                client.call_api("/pet/{petId}", "GET", path_params={"petId": 999})

        assert exc_info.value.status == 404

    def test_connection_error(self):
        cfg = Configuration(host="https://api.example.com")
        client = ApiClient(configuration=cfg)

        with patch.object(httpx.Client, "request", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(ApiException) as exc_info:
                client.call_api("/test", "GET")

        assert exc_info.value.status == 0
        assert "Connection error" in exc_info.value.reason

    def test_timeout_error(self):
        cfg = Configuration(host="https://api.example.com")
        client = ApiClient(configuration=cfg)

        with patch.object(httpx.Client, "request", side_effect=httpx.ReadTimeout("timed out")):
            with pytest.raises(ApiException) as exc_info:
                client.call_api("/slow", "GET")

        assert exc_info.value.status == 0
        assert "timed out" in exc_info.value.reason

    def test_return_http_info(self):
        cfg = Configuration(host="https://api.example.com")
        client = ApiClient(configuration=cfg)

        mock_resp = self._mock_response(
            status=200,
            json_data={"ok": True},
            headers={"X-Custom": "val"},
        )

        with patch.object(httpx.Client, "request", return_value=mock_resp):
            data, status, headers = client.call_api("/test", "GET", _return_http_info=True)

        assert data == {"ok": True}
        assert status == 200
        assert "x-custom" in headers

    def test_text_response(self):
        cfg = Configuration(host="https://api.example.com")
        client = ApiClient(configuration=cfg)

        resp = httpx.Response(
            status_code=200,
            headers={"content-type": "text/plain"},
            text="hello world",
        )

        with patch.object(httpx.Client, "request", return_value=resp):
            result = client.call_api("/text", "GET")

        assert result == "hello world"

    def test_string_body_json(self):
        cfg = Configuration(host="https://api.example.com")
        client = ApiClient(configuration=cfg)

        mock_resp = self._mock_response(json_data={"ok": True})

        with patch.object(httpx.Client, "request", return_value=mock_resp) as mock_req:
            client.call_api("/data", "POST", body='{"key": "value"}')

        # String that is valid JSON should be parsed
        assert mock_req.call_args.kwargs["json"] == {"key": "value"}

    def test_string_body_plain(self):
        cfg = Configuration(host="https://api.example.com")
        client = ApiClient(configuration=cfg)

        mock_resp = self._mock_response(json_data={"ok": True})

        with patch.object(httpx.Client, "request", return_value=mock_resp) as mock_req:
            client.call_api("/data", "POST", body="plain text body")

        assert mock_req.call_args.kwargs["content"] == "plain text body"

    def test_header_params(self):
        cfg = Configuration(host="https://api.example.com")
        client = ApiClient(configuration=cfg)

        mock_resp = self._mock_response(json_data={})

        with patch.object(httpx.Client, "request", return_value=mock_resp) as mock_req:
            client.call_api(
                "/data",
                "GET",
                header_params={"X-Request-Id": "abc-123"},
            )

        headers = mock_req.call_args.kwargs["headers"]
        assert headers["X-Request-Id"] == "abc-123"

    def test_trailing_slash_host(self):
        cfg = Configuration(host="https://api.example.com/")
        client = ApiClient(configuration=cfg)

        mock_resp = self._mock_response(json_data={})

        with patch.object(httpx.Client, "request", return_value=mock_resp) as mock_req:
            client.call_api("/pet", "GET")

        assert mock_req.call_args.kwargs["url"] == "https://api.example.com/pet"


# =========================================================================
# Exceptions
# =========================================================================


class TestExceptions:
    """Exception hierarchy tests."""

    def test_api_exception_str(self):
        exc = ApiException(status=500, reason="Internal Server Error")
        assert "500" in str(exc)
        assert "Internal Server Error" in str(exc)

    def test_api_exception_body(self):
        exc = ApiException(status=422, reason="Unprocessable", body='{"detail":"bad"}')
        assert exc.body == '{"detail":"bad"}'

    def test_api_exception_inherits(self):
        assert issubclass(ApiException, OpenApiException)

    def test_api_type_error_inherits(self):
        assert issubclass(ApiTypeError, (OpenApiException, TypeError))
        exc = ApiTypeError("bad type", path_to_item=["a", "b"])
        assert exc.path_to_item == ["a", "b"]

    def test_api_value_error_inherits(self):
        assert issubclass(ApiValueError, (OpenApiException, ValueError))

    def test_api_key_error_inherits(self):
        assert issubclass(ApiKeyError, (OpenApiException, KeyError))

    def test_api_attribute_error_inherits(self):
        assert issubclass(ApiAttributeError, (OpenApiException, AttributeError))


# =========================================================================
# ApiResponse
# =========================================================================


class TestApiResponse:
    """ApiResponse wrapper tests."""

    def test_defaults(self):
        resp = ApiResponse()
        assert resp.status_code == 0
        assert resp.headers == {}
        assert resp.data is None
        assert resp.raw_data is None

    def test_full(self):
        resp = ApiResponse(
            status_code=200,
            headers={"X-Custom": "val"},
            data={"id": 1},
            raw_data=b'{"id": 1}',
        )
        assert resp.status_code == 200
        assert resp.data == {"id": 1}
        assert resp.raw_data == b'{"id": 1}'
