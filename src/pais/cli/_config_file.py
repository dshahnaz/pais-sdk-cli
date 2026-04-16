"""TOML config-file loader with profile selection and secrets policy.

Discovery order (first hit wins, no merging across files):
    1. explicit `path` argument (CLI --config)
    2. PAIS_CONFIG env var
    3. ./pais.toml
    4. ~/.pais/config.toml

Profile resolution:
    1. explicit `profile` argument (CLI --profile)
    2. PAIS_PROFILE env var
    3. `default_profile` field in the loaded file
    4. "default"

Secrets policy: keys in `_FORBIDDEN_KEYS` are rejected outright with an
ImportError-style message pointing the user to env vars / keyring.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - py3.10 fallback
    import tomli as tomllib

_FORBIDDEN_KEYS = frozenset({"password", "client_secret", "bearer_token"})

PROJECT_FILENAME = "pais.toml"
GLOBAL_PATH = Path.home() / ".pais" / "config.toml"


class ConfigError(ValueError):
    """A config file is unparseable, structurally wrong, or contains a forbidden key."""


def discover_config_path(explicit: Path | str | None = None) -> Path | None:
    """Return the config file path that will be used, or None if none is found."""
    if explicit is not None:
        p = Path(explicit).expanduser()
        return p if p.exists() else None
    env = os.environ.get("PAIS_CONFIG")
    if env:
        p = Path(env).expanduser()
        return p if p.exists() else None
    project = Path.cwd() / PROJECT_FILENAME
    if project.exists():
        return project
    if GLOBAL_PATH.exists():
        return GLOBAL_PATH
    return None


def load_profile(
    *,
    path: Path | str | None = None,
    profile: str | None = None,
) -> tuple[dict[str, Any], Path | None, str]:
    """Load the active profile from a config file.

    Returns: (settings_dict, config_file_used, profile_name_resolved).
    If no config file is found, returns ({}, None, profile or 'default').
    """
    cfg_path = discover_config_path(path)
    if cfg_path is None:
        return {}, None, profile or os.environ.get("PAIS_PROFILE") or "default"

    try:
        raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{cfg_path}: invalid TOML: {e}") from e

    name = profile or os.environ.get("PAIS_PROFILE") or raw.get("default_profile") or "default"

    profiles = raw.get("profiles", {})
    if not isinstance(profiles, dict):
        raise ConfigError(f"{cfg_path}: 'profiles' must be a table")

    if not profiles:
        # Allow a flat config file with no [profiles.X] sections — treat top-level
        # keys (other than `default_profile`) as the active profile.
        flat = {k: v for k, v in raw.items() if k != "default_profile"}
        _check_forbidden(cfg_path, name, flat)
        return flat, cfg_path, name

    if name not in profiles:
        available = ", ".join(sorted(profiles)) or "(none)"
        raise ConfigError(f"{cfg_path}: profile '{name}' not found. Available: {available}")

    section = profiles[name]
    if not isinstance(section, dict):
        raise ConfigError(f"{cfg_path}: [profiles.{name}] must be a table")
    _check_forbidden(cfg_path, name, section)
    return dict(section), cfg_path, name


def _check_forbidden(path: Path, profile: str, data: dict[str, Any]) -> None:
    bad = sorted(k for k in data if k.lower() in _FORBIDDEN_KEYS)
    if bad:
        raise ConfigError(
            f"{path} [profile={profile}]: secret keys are not allowed in the config file: "
            f"{', '.join(bad)}.\n"
            "Set them via env vars (PAIS_PASSWORD, PAIS_CLIENT_SECRET, PAIS_BEARER_TOKEN) "
            "or your OS keyring."
        )


SCAFFOLD_GLOBAL = """\
# PAIS CLI config — ~/.pais/config.toml
#
# Profiles let you switch between environments without re-exporting env vars.
# Pick the active profile via:
#   pais --profile <name> ...
# or set PAIS_PROFILE=<name>.
#
# Secrets (password, client_secret, bearer_token) are NOT allowed in this file.
# Set them via env vars (PAIS_PASSWORD, ...) or an OS keyring.

default_profile = "lab"

[profiles.lab]
mode = "http"
base_url = "https://pais.internal/api/v1"
auth = "none"
verify_ssl = false
log_level = "INFO"

# [profiles.prod]
# mode = "http"
# base_url = "https://pais.example.com/api/v1"
# auth = "oidc_password"
# verify_ssl = true
# oidc_issuer = "https://pais.example.com"
# client_id = "pais-cli"
# username = "alice"
# # password comes from PAIS_PASSWORD
"""

SCAFFOLD_PROJECT = """\
# PAIS CLI config — ./pais.toml (project-local; overrides ~/.pais/config.toml).

default_profile = "default"

[profiles.default]
mode = "mock"
"""
