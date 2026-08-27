"""Configuration module for OpenAPI clients."""

from __future__ import annotations


class Configuration:
    """OpenAPI client configuration.

    Manages settings for API client connections including host, authentication,
    and SSL configuration.
    """

    _default: Configuration | None = None

    def __init__(
        self,
        host: str = "http://localhost",
        api_key: dict[str, str] | None = None,
        api_key_prefix: dict[str, str] | None = None,
        access_token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        ssl_ca_cert: str | None = None,
        server_index: int | None = None,
        server_variables: dict | None = None,
    ) -> None:
        self.host = host
        self.api_key: dict[str, str] = api_key or {}
        self.api_key_prefix: dict[str, str] = api_key_prefix or {}
        self.access_token = access_token
        self.username = username
        self.password = password
        self.ssl_ca_cert = ssl_ca_cert
        self.server_index = server_index
        self.server_variables: dict = server_variables or {}
        self.verify_ssl = True
        self.temp_folder_path: str | None = None

    @classmethod
    def get_default(cls) -> Configuration:
        """Return the default configuration singleton."""
        if cls._default is None:
            cls._default = Configuration()
        return cls._default

    @classmethod
    def set_default(cls, default: Configuration) -> None:
        """Set the default configuration."""
        cls._default = default
