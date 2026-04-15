"""Auth strategy tests."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest

from pais.auth.bearer import BearerAuth
from pais.auth.none import NoAuth
from pais.auth.oidc_password import OIDCPasswordAuth


def test_no_auth_attaches_nothing() -> None:
    headers: dict[str, str] = {}
    NoAuth().apply(headers)
    assert headers == {}
    assert NoAuth().refresh() is False


def test_bearer_auth_attaches() -> None:
    headers: dict[str, str] = {}
    BearerAuth("abc").apply(headers)
    assert headers == {"Authorization": "Bearer abc"}
    assert BearerAuth("abc").refresh() is False


def test_bearer_auth_rejects_empty_token() -> None:
    with pytest.raises(ValueError):
        BearerAuth("")


def _oidc_handler_factory(
    token_response: dict[str, Any], seen: dict[str, Any]
) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={"token_endpoint": "https://issuer.test/token"},
            )
        if req.url.path.endswith("/token"):
            seen["body"] = dict(httpx.QueryParams(req.content.decode()))
            return httpx.Response(200, json=token_response)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_oidc_password_flow_fetches_and_caches(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}
    transport = _oidc_handler_factory(
        {"access_token": "ACCESS", "refresh_token": "REFRESH", "expires_in": 600},
        seen,
    )
    http = httpx.Client(transport=transport)

    cache = tmp_path / "token.json"
    auth = OIDCPasswordAuth(
        issuer="https://issuer.test",
        client_id="cli",
        username="alice",
        password="p@ss",
        cache_path=cache,
        http_client=http,
    )

    headers: dict[str, str] = {}
    auth.apply(headers)
    assert headers["Authorization"] == "Bearer ACCESS"
    assert seen["body"]["grant_type"] == "password"
    assert seen["body"]["username"] == "alice"

    # cache file exists with mode 0600 (on POSIX)
    assert cache.exists()
    if os.name == "posix":
        mode = stat.S_IMODE(cache.stat().st_mode)
        assert mode == 0o600

    # cache key structure
    payload = json.loads(cache.read_text())
    assert "https://issuer.test|cli|alice" in payload


def test_oidc_password_reuses_cached_fresh_token(tmp_path: Path) -> None:
    """Second apply() should NOT hit the network if the cached token is fresh."""
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if req.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"token_endpoint": "https://issuer.test/token"})
        return httpx.Response(
            200, json={"access_token": "T1", "refresh_token": "R1", "expires_in": 600}
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    cache = tmp_path / "token.json"
    auth = OIDCPasswordAuth(
        issuer="https://issuer.test",
        client_id="cli",
        username="alice",
        password="pw",
        cache_path=cache,
        http_client=http,
    )
    auth.apply({})
    first = calls["n"]
    auth.apply({})
    auth.apply({})
    assert calls["n"] == first  # no extra calls


def test_oidc_cache_survives_new_instance(tmp_path: Path) -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if req.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"token_endpoint": "https://issuer.test/token"})
        return httpx.Response(
            200, json={"access_token": "T1", "refresh_token": "R1", "expires_in": 600}
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    cache = tmp_path / "token.json"

    a1 = OIDCPasswordAuth(
        issuer="https://issuer.test",
        client_id="cli",
        username="alice",
        password="pw",
        cache_path=cache,
        http_client=http,
    )
    a1.apply({})
    initial = calls["n"]

    # New instance, same cache file: should NOT re-hit the network.
    a2 = OIDCPasswordAuth(
        issuer="https://issuer.test",
        client_id="cli",
        username="alice",
        password="pw",
        cache_path=cache,
        http_client=http,
    )
    headers: dict[str, str] = {}
    a2.apply(headers)
    assert headers["Authorization"] == "Bearer T1"
    assert calls["n"] == initial


def test_oidc_refresh_forces_new_token(tmp_path: Path) -> None:
    tokens = iter(
        [
            {"access_token": "OLD", "refresh_token": "R", "expires_in": 600},
            {"access_token": "NEW", "refresh_token": "R2", "expires_in": 600},
        ]
    )

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json={"token_endpoint": "https://issuer.test/token"})
        return httpx.Response(200, json=next(tokens))

    http = httpx.Client(transport=httpx.MockTransport(handler))
    cache = tmp_path / "token.json"
    auth = OIDCPasswordAuth(
        issuer="https://issuer.test",
        client_id="cli",
        username="alice",
        password="pw",
        cache_path=cache,
        http_client=http,
    )
    h1: dict[str, str] = {}
    auth.apply(h1)
    assert h1["Authorization"] == "Bearer OLD"
    assert auth.refresh() is True
    h2: dict[str, str] = {}
    auth.apply(h2)
    assert h2["Authorization"] == "Bearer NEW"


def test_oidc_cache_key_isolates_usernames(tmp_path: Path) -> None:
    """Different usernames share the file but not the token — multi-profile."""

    def handler_for(tok: str):
        def h(req: httpx.Request) -> httpx.Response:
            if req.url.path.endswith("/.well-known/openid-configuration"):
                return httpx.Response(200, json={"token_endpoint": "https://issuer.test/token"})
            return httpx.Response(
                200,
                json={"access_token": tok, "refresh_token": "r", "expires_in": 600},
            )

        return h

    cache = tmp_path / "token.json"
    OIDCPasswordAuth(
        issuer="https://issuer.test",
        client_id="cli",
        username="alice",
        password="pw",
        cache_path=cache,
        http_client=httpx.Client(transport=httpx.MockTransport(handler_for("ALICE_T"))),
    ).apply({})
    OIDCPasswordAuth(
        issuer="https://issuer.test",
        client_id="cli",
        username="bob",
        password="pw",
        cache_path=cache,
        http_client=httpx.Client(transport=httpx.MockTransport(handler_for("BOB_T"))),
    ).apply({})

    payload = json.loads(cache.read_text())
    assert payload["https://issuer.test|cli|alice"]["access_token"] == "ALICE_T"
    assert payload["https://issuer.test|cli|bob"]["access_token"] == "BOB_T"
