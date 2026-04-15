"""Configuration model — single source of truth for all runtime tunables."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    from pais.client import PaisClient


Mode = Literal["mock", "http"]
AuthMode = Literal["none", "bearer", "oidc_password"]


class Settings(BaseSettings):
    """Env-driven settings. Precedence: CLI flag > env var > ~/.pais/config.toml > defaults."""

    model_config = SettingsConfigDict(
        env_prefix="PAIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mode: Mode = "mock"
    base_url: str = "http://localhost:8080/api/v1"
    auth: AuthMode = "none"
    verify_ssl: bool = True

    # OIDC (only read when auth=oidc_password)
    oidc_issuer: str | None = None
    client_id: str | None = None
    client_secret: SecretStr | None = None
    username: str | None = None
    password: SecretStr | None = None

    # Bearer (only read when auth=bearer)
    bearer_token: SecretStr | None = None

    # Timeouts (seconds)
    connect_timeout: float = 5.0
    read_timeout: float = 60.0
    total_timeout: float = 120.0

    # Retry policy
    retry_max_attempts: int = 4
    retry_base_delay: float = 0.25
    retry_max_delay: float = 10.0
    chat_cold_start_retries: int = 3
    chat_cold_start_delay: float = 3.0

    # Logging
    log_level: str = "INFO"
    log_file: Path | None = Field(default=Path.home() / ".pais" / "logs" / "pais.log")
    log_json_console: bool = False

    # Token cache
    token_cache_path: Path = Field(default=Path.home() / ".pais" / "token.json")

    # Profile name (read from ~/.pais/config.toml[profile])
    profile: str | None = None

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    def build_client(self) -> PaisClient:
        """Construct a fully-wired PaisClient from these settings."""
        from pais.client import PaisClient

        return PaisClient.from_settings(self)
