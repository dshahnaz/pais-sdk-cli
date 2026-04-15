"""OIDC Resource Owner Password Flow with on-disk token cache (mode 0600)."""

from __future__ import annotations

import contextlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from pais.logging import get_logger

_log = get_logger("pais.auth.oidc")


@dataclass
class _CachedToken:
    access_token: str
    refresh_token: str | None
    expires_at: float  # epoch seconds

    def is_fresh(self, skew: float = 30.0) -> bool:
        return time.time() + skew < self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> _CachedToken:
        return cls(
            access_token=raw["access_token"],
            refresh_token=raw.get("refresh_token"),
            expires_at=float(raw["expires_at"]),
        )


class OIDCPasswordAuth:
    """Acquire tokens via OIDC Resource Owner Password Flow, cache to disk at 0600.

    Token endpoint is discovered from `{issuer}/.well-known/openid-configuration`.
    Cache key = (issuer, client_id, username) — so multiple profiles coexist.
    """

    def __init__(
        self,
        *,
        issuer: str,
        client_id: str,
        username: str,
        password: str,
        client_secret: str | None = None,
        cache_path: Path,
        verify_ssl: bool = True,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._issuer = issuer.rstrip("/")
        self._client_id = client_id
        self._username = username
        self._password = password
        self._client_secret = client_secret
        self._cache_path = cache_path
        self._verify_ssl = verify_ssl
        self._client = http_client
        self._token_endpoint: str | None = None
        self._token: _CachedToken | None = self._load_cache()

    # ---- Header injection / refresh protocol ---------------------------------
    def apply(self, headers: dict[str, str]) -> None:
        tok = self._ensure_token()
        headers["Authorization"] = f"Bearer {tok.access_token}"

    def refresh(self) -> bool:
        self._token = None  # force re-acquisition
        try:
            self._ensure_token()
            return True
        except Exception as e:  # pragma: no cover - defensive
            _log.warning("oidc.refresh_failed", error=str(e))
            return False

    # ---- Internals -----------------------------------------------------------
    def _cache_key(self) -> str:
        return f"{self._issuer}|{self._client_id}|{self._username}"

    def _load_cache(self) -> _CachedToken | None:
        if not self._cache_path.exists():
            return None
        try:
            payload = json.loads(self._cache_path.read_text())
            entry = payload.get(self._cache_key())
            if not entry:
                return None
            return _CachedToken.from_dict(entry)
        except Exception:
            return None

    def _save_cache(self, tok: _CachedToken) -> None:
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, Any] = {}
        if self._cache_path.exists():
            try:
                existing = json.loads(self._cache_path.read_text())
            except Exception:
                existing = {}
        existing[self._cache_key()] = tok.to_dict()
        self._cache_path.write_text(json.dumps(existing))
        with contextlib.suppress(OSError):  # best effort on non-POSIX
            os.chmod(self._cache_path, 0o600)

    def _discover_token_endpoint(self) -> str:
        if self._token_endpoint:
            return self._token_endpoint
        client = self._client or httpx.Client(verify=self._verify_ssl, timeout=10.0)
        try:
            resp = client.get(f"{self._issuer}/.well-known/openid-configuration")
            resp.raise_for_status()
            self._token_endpoint = resp.json()["token_endpoint"]
        finally:
            if self._client is None:
                client.close()
        assert self._token_endpoint is not None
        return self._token_endpoint

    def _ensure_token(self) -> _CachedToken:
        if self._token and self._token.is_fresh():
            return self._token
        # Try refresh_token first if we have one.
        if self._token and self._token.refresh_token:
            tok = self._do_refresh(self._token.refresh_token)
            if tok is not None:
                self._token = tok
                self._save_cache(tok)
                return tok
        tok = self._do_password()
        self._token = tok
        self._save_cache(tok)
        return tok

    def _do_password(self) -> _CachedToken:
        endpoint = self._discover_token_endpoint()
        data = {
            "grant_type": "password",
            "client_id": self._client_id,
            "username": self._username,
            "password": self._password,
            "scope": "openid profile",
        }
        if self._client_secret:
            data["client_secret"] = self._client_secret
        return self._post_token(endpoint, data)

    def _do_refresh(self, refresh_token: str) -> _CachedToken | None:
        try:
            endpoint = self._discover_token_endpoint()
        except Exception:
            return None
        data = {
            "grant_type": "refresh_token",
            "client_id": self._client_id,
            "refresh_token": refresh_token,
        }
        if self._client_secret:
            data["client_secret"] = self._client_secret
        try:
            return self._post_token(endpoint, data)
        except Exception:
            return None

    def _post_token(self, endpoint: str, data: dict[str, str]) -> _CachedToken:
        client = self._client or httpx.Client(verify=self._verify_ssl, timeout=10.0)
        try:
            resp = client.post(endpoint, data=data)
            resp.raise_for_status()
            payload = resp.json()
        finally:
            if self._client is None:
                client.close()
        now = time.time()
        expires_in = float(payload.get("expires_in", 300))
        return _CachedToken(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=now + expires_in,
        )
