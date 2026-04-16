"""TOML config-file loader and Settings precedence tests."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from pais.cli._config_file import ConfigError, discover_config_path, load_profile
from pais.config import Settings, set_runtime_overrides


@pytest.fixture(autouse=True)
def _isolate_overrides() -> None:
    set_runtime_overrides(config_path=None, profile=None)
    yield
    set_runtime_overrides(config_path=None, profile=None)


def _write_toml(p: Path, body: str) -> Path:
    p.write_text(textwrap.dedent(body).lstrip())
    return p


def test_discovery_explicit_path_wins(tmp_path: Path) -> None:
    cfg = _write_toml(tmp_path / "x.toml", "[profiles.default]\nmode = 'mock'\n")
    assert discover_config_path(cfg) == cfg


def test_discovery_returns_none_when_nothing_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PAIS_CONFIG", raising=False)
    monkeypatch.setattr("pais.cli._config_file.GLOBAL_PATH", tmp_path / "missing.toml")
    assert discover_config_path() is None


def test_load_profile_picks_default_profile(tmp_path: Path) -> None:
    cfg = _write_toml(
        tmp_path / "c.toml",
        """
        default_profile = "lab"
        [profiles.lab]
        mode = "http"
        base_url = "https://lab.example/api/v1"
        [profiles.prod]
        mode = "http"
        base_url = "https://prod.example/api/v1"
        """,
    )
    data, used_path, used_profile = load_profile(path=cfg)
    assert used_path == cfg
    assert used_profile == "lab"
    assert data["base_url"] == "https://lab.example/api/v1"


def test_load_profile_explicit_argument_overrides_default(tmp_path: Path) -> None:
    cfg = _write_toml(
        tmp_path / "c.toml",
        """
        default_profile = "lab"
        [profiles.lab]
        mode = "http"
        [profiles.prod]
        mode = "http"
        base_url = "https://prod/api/v1"
        """,
    )
    data, _, name = load_profile(path=cfg, profile="prod")
    assert name == "prod"
    assert data["base_url"] == "https://prod/api/v1"


def test_load_profile_unknown_profile_raises(tmp_path: Path) -> None:
    cfg = _write_toml(tmp_path / "c.toml", "[profiles.lab]\nmode = 'mock'\n")
    with pytest.raises(ConfigError, match="profile 'missing' not found"):
        load_profile(path=cfg, profile="missing")


def test_load_profile_rejects_secret_keys(tmp_path: Path) -> None:
    cfg = _write_toml(
        tmp_path / "c.toml",
        """
        [profiles.lab]
        mode = "http"
        password = "supersecret"
        """,
    )
    with pytest.raises(ConfigError, match="secret keys are not allowed"):
        load_profile(path=cfg, profile="lab")


def test_load_profile_invalid_toml_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "bad.toml"
    cfg.write_text("not [ valid")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_profile(path=cfg)


def test_settings_uses_config_file(tmp_path: Path) -> None:
    cfg = _write_toml(
        tmp_path / "c.toml",
        """
        default_profile = "lab"
        [profiles.lab]
        mode = "http"
        base_url = "https://from-file/api/v1"
        auth = "none"
        verify_ssl = false
        """,
    )
    set_runtime_overrides(config_path=cfg, profile="lab")
    s = Settings()
    assert s.mode == "http"
    assert s.base_url == "https://from-file/api/v1"
    assert s.verify_ssl is False


def test_env_overrides_config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = _write_toml(
        tmp_path / "c.toml",
        """
        [profiles.default]
        mode = "http"
        base_url = "https://from-file/api/v1"
        """,
    )
    set_runtime_overrides(config_path=cfg, profile="default")
    monkeypatch.setenv("PAIS_BASE_URL", "https://from-env/api/v1")
    s = Settings()
    assert s.base_url == "https://from-env/api/v1"


def test_constructor_kwarg_overrides_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAIS_BASE_URL", "https://env/api/v1")
    s = Settings(base_url="https://kwarg/api/v1")
    assert s.base_url == "https://kwarg/api/v1"


def test_no_config_file_falls_back_to_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PAIS_CONFIG", raising=False)
    monkeypatch.setattr("pais.cli._config_file.GLOBAL_PATH", tmp_path / "no-such.toml")
    s = Settings()
    assert s.mode == "mock"
    assert s.base_url == "http://localhost:8080/api/v1"


def test_flat_config_file_without_profiles_section(tmp_path: Path) -> None:
    """A simple config without [profiles.X] is treated as the active profile."""
    cfg = _write_toml(
        tmp_path / "c.toml",
        """
        mode = "http"
        base_url = "https://flat/api/v1"
        verify_ssl = false
        """,
    )
    data, _, _ = load_profile(path=cfg)
    assert data["base_url"] == "https://flat/api/v1"
    assert data["verify_ssl"] is False
