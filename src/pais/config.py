"""Configuration model — single source of truth for all runtime tunables.

Settings precedence (highest to lowest):
    1. constructor kwargs / CLI flags
    2. PAIS_* environment variables
    3. ~/.pais/config.toml or ./pais.toml profile (loaded via cli._config_file)
    4. .env file (legacy)
    5. Settings defaults
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

if TYPE_CHECKING:
    from pais.client import PaisClient


Mode = Literal["mock", "http"]
AuthMode = Literal["none", "bearer", "oidc_password"]


# Internal env vars (read at module load) so the config-file source knows
# which path / profile to use without us re-implementing precedence logic.
_CONFIG_PATH_ENV = "PAIS_CONFIG"
_PROFILE_ENV = "PAIS_PROFILE"

# Module-level overrides settable by the CLI before constructing Settings()
# so command-line --config / --profile beat env vars without polluting argv.
_active_config_path: Path | None = None
_active_profile: str | None = None


def set_runtime_overrides(*, config_path: Path | None = None, profile: str | None = None) -> None:
    """Used by the CLI to pin --config / --profile before Settings() is built."""
    global _active_config_path, _active_profile
    _active_config_path = config_path
    _active_profile = profile


class _ConfigFileSource(PydanticBaseSettingsSource):
    """Pydantic-settings source backed by the TOML config-file loader."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        from pais.cli._config_file import load_profile

        # Resolve path/profile at construction so each Settings() reads
        # the *current* CLI overrides + env at that moment.
        path = _active_config_path
        profile = _active_profile or os.environ.get(_PROFILE_ENV)
        try:
            data, used_path, used_profile = load_profile(path=path, profile=profile)
        except Exception:
            # Defer config errors to the CLI layer so Settings() doesn't blow up
            # in unrelated contexts (e.g. unit tests that import Settings).
            data, used_path, used_profile = {}, None, profile or "default"
        self._data = data
        self.used_path = used_path
        self.used_profile = used_profile

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        if field_name in self._data:
            return self._data[field_name], field_name, False
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(self._data)


class Settings(BaseSettings):
    """Env- and file-driven settings."""

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
    chat_retry_on_empty: bool = True

    # Logging
    log_level: str = "INFO"
    log_file: Path | None = Field(default=Path.home() / ".pais" / "logs" / "pais.log")
    log_json_console: bool = False

    # Token cache
    token_cache_path: Path = Field(default=Path.home() / ".pais" / "token.json")

    # Profile name (informational only; selection happens before Settings()).
    profile: str | None = None

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Precedence: init kwargs > env > config file > .env > secrets dir.
        return (
            init_settings,
            env_settings,
            _ConfigFileSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )

    def build_client(self) -> PaisClient:
        """Construct a fully-wired PaisClient from these settings."""
        from pais.client import PaisClient

        return PaisClient.from_settings(self)
