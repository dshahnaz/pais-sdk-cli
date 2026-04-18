"""Transport + auth tests. Uses httpx MockTransport, no network."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from pais.auth.bearer import BearerAuth
from pais.auth.none import NoAuth
from pais.errors import (
    PaisAuthError,
    PaisRateLimitError,
    PaisServerError,
    PaisTimeoutError,
    PaisValidationError,
)
from pais.transport.httpx_transport import HttpxTransport


def _make_transport(
    handler: Callable[[httpx.Request], httpx.Response],
    **kwargs: Any,
) -> HttpxTransport:
    mock = httpx.MockTransport(handler)
    t = HttpxTransport("http://testserver/api/v1", **kwargs)
    # swap the underlying client for one backed by MockTransport
    t._client.close()
    t._client = httpx.Client(base_url=t.base_url, transport=mock, timeout=t._client.timeout)
    return t


def test_no_auth_sends_no_authorization_header() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["authorization"] = req.headers.get("authorization")
        seen["x_request_id"] = req.headers.get("x-request-id")
        return httpx.Response(200, json={"ok": True})

    t = _make_transport(handler, auth=NoAuth())
    resp = t.request("GET", "/knowledge-bases")
    assert resp.ok
    assert seen["authorization"] is None
    assert seen["x_request_id"] is not None


def test_bearer_auth_attaches_header() -> None:
    seen: dict[str, Any] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["authorization"] = req.headers.get("authorization")
        return httpx.Response(200, json={})

    t = _make_transport(handler, auth=BearerAuth("tok-123"))
    t.request("GET", "/knowledge-bases")
    assert seen["authorization"] == "Bearer tok-123"


def test_retry_on_500_then_success() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, json={"detail": "boom"})
        return httpx.Response(200, json={"id": "kb_1"})

    t = _make_transport(handler, retry_max_attempts=5, retry_base_delay=0.0, retry_max_delay=0.0)
    resp = t.request("GET", "/knowledge-bases/kb_1")
    assert resp.ok
    assert calls["n"] == 3


def test_429_uses_retry_after() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"detail": "slow"}, headers={"Retry-After": "0"})
        return httpx.Response(200, json={})

    t = _make_transport(handler, retry_max_attempts=3, retry_base_delay=0.0)
    t.request("GET", "/knowledge-bases")
    assert calls["n"] == 2


def test_429_exhausted_raises_rate_limit() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "limit"}, headers={"Retry-After": "0"})

    t = _make_transport(handler, retry_max_attempts=2, retry_base_delay=0.0)
    with pytest.raises(PaisRateLimitError):
        t.request("GET", "/knowledge-bases")


def test_validation_error_not_retried() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            422,
            json={"detail": [{"error_code": "missing", "loc": ["body", "name"]}]},
        )

    t = _make_transport(handler, retry_max_attempts=4, retry_base_delay=0.0)
    with pytest.raises(PaisValidationError):
        t.request("POST", "/knowledge-bases", json={})
    assert calls["n"] == 1  # 422 is not retryable


def test_auth_refresh_on_401() -> None:
    calls = {"n": 0, "refresh_called": 0}

    class Refreshable:
        def __init__(self) -> None:
            self.token = "old"

        def apply(self, headers: dict[str, str]) -> None:
            headers["Authorization"] = f"Bearer {self.token}"

        def refresh(self) -> bool:
            calls["refresh_called"] += 1
            self.token = "new"
            return True

    auth = Refreshable()

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        tok = req.headers.get("authorization", "")
        if "new" in tok:
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(401, json={"detail": "expired"})

    t = _make_transport(handler, auth=auth, retry_max_attempts=2, retry_base_delay=0.0)
    resp = t.request("GET", "/knowledge-bases")
    assert resp.ok
    assert calls["refresh_called"] == 1
    assert calls["n"] == 2


def test_auth_refresh_fails_raises_auth_error() -> None:
    class NoRefresh:
        def apply(self, headers: dict[str, str]) -> None:
            headers["Authorization"] = "Bearer stale"

        def refresh(self) -> bool:
            return False

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "unauth"})

    t = _make_transport(handler, auth=NoRefresh(), retry_max_attempts=2, retry_base_delay=0.0)
    with pytest.raises(PaisAuthError):
        t.request("GET", "/knowledge-bases")


def test_chat_502_cold_start_retry() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(502, json={"detail": "cold"})
        return httpx.Response(200, json={"id": "chatcmpl-1", "object": "chat.completion"})

    t = _make_transport(
        handler,
        retry_max_attempts=1,  # normal retries disabled
        chat_cold_start_retries=5,
        chat_cold_start_delay=0.0,
        retry_base_delay=0.0,
    )
    resp = t.request(
        "POST",
        "/compatibility/openai/v1/agents/a1/chat/completions",
        json={"messages": []},
    )
    assert resp.ok
    assert calls["n"] == 3


def test_non_chat_502_not_special_cased() -> None:
    """502 on non-chat path uses the normal retry policy, not the chat cold-start loop."""
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(502, json={"detail": "bad"})

    t = _make_transport(
        handler,
        retry_max_attempts=2,
        chat_cold_start_retries=10,
        retry_base_delay=0.0,
    )
    with pytest.raises(PaisServerError):
        t.request("GET", "/knowledge-bases")
    # only retry_max_attempts (2) attempts
    assert calls["n"] == 2


def test_timeout_raises_pais_timeout() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    t = _make_transport(handler, retry_max_attempts=2, retry_base_delay=0.0)
    with pytest.raises(PaisTimeoutError):
        t.request("GET", "/knowledge-bases")


def test_request_id_propagates_from_response_header() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"ok": True}, headers={"X-Request-ID": "server-generated-42"}
        )

    t = _make_transport(handler)
    resp = t.request("GET", "/knowledge-bases")
    assert resp.request_id == "server-generated-42"


def test_stream_yields_bytes_and_errors_on_4xx() -> None:
    seen: list[bytes] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("bad"):
            return httpx.Response(403, json={"detail": "forbidden"})
        return httpx.Response(
            200,
            content=b"data: chunk1\n\ndata: chunk2\n\n",
            headers={"Content-Type": "text/event-stream"},
        )

    t = _make_transport(handler)
    for chunk in t.stream("POST", "/chat/completions", json={}):
        seen.append(chunk)
    assert b"".join(seen) == b"data: chunk1\n\ndata: chunk2\n\n"

    with pytest.raises(PaisAuthError):
        list(t.stream("POST", "/bad"))


CHAT_PATH = "/compatibility/openai/v1/agents/a1/chat/completions"


def _empty_chat_body() -> dict[str, Any]:
    return {
        "id": "chatcmpl-empty",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10},
    }


def _full_chat_body() -> dict[str, Any]:
    return {
        "id": "chatcmpl-ok",
        "object": "chat.completion",
        "created": 1,
        "model": "m",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
    }


def test_chat_empty_content_triggers_retry() -> None:
    """200 with empty content on a chat path triggers one more attempt."""
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=_empty_chat_body())
        return httpx.Response(200, json=_full_chat_body())

    t = _make_transport(
        handler,
        retry_max_attempts=1,
        chat_cold_start_retries=3,
        chat_cold_start_delay=0.0,
        retry_base_delay=0.0,
    )
    resp = t.request("POST", CHAT_PATH, json={"messages": []})
    assert resp.ok
    assert calls["n"] == 2
    assert resp.body["choices"][0]["message"]["content"] == "hi"


def test_chat_empty_content_exhausts_retries_then_returns() -> None:
    """All attempts empty → caller gets the last (empty) response, no raise."""
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_empty_chat_body())

    t = _make_transport(
        handler,
        retry_max_attempts=1,
        chat_cold_start_retries=3,
        chat_cold_start_delay=0.0,
        retry_base_delay=0.0,
    )
    resp = t.request("POST", CHAT_PATH, json={"messages": []})
    assert resp.ok
    assert calls["n"] == 3
    assert resp.body["choices"][0]["message"]["content"] == ""


def test_non_chat_200_empty_body_not_special_cased() -> None:
    """200 empty body on a non-chat path returns immediately — no retry."""
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={})

    t = _make_transport(
        handler,
        retry_max_attempts=4,
        chat_cold_start_retries=4,
        chat_cold_start_delay=0.0,
        retry_base_delay=0.0,
    )
    resp = t.request("GET", "/knowledge-bases/kb_1")
    assert resp.ok
    assert calls["n"] == 1


def test_chat_retry_on_empty_disabled() -> None:
    """chat_retry_on_empty=False → empty chat 200 returns unchanged without retry."""
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_empty_chat_body())

    t = _make_transport(
        handler,
        retry_max_attempts=1,
        chat_cold_start_retries=3,
        chat_cold_start_delay=0.0,
        chat_retry_on_empty=False,
        retry_base_delay=0.0,
    )
    resp = t.request("POST", CHAT_PATH, json={"messages": []})
    assert resp.ok
    assert calls["n"] == 1


def test_pais_response_chat_log_emits_diagnostic_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful chat 200 emits `pais.response.chat` with finish_reason + tokens."""
    from pais.transport import httpx_transport as ht

    calls: list[dict[str, Any]] = []
    real_info = ht._log.info

    def spy_info(event: str, **kw: Any) -> None:
        if event == "pais.response.chat":
            calls.append(kw)
        real_info(event, **kw)

    monkeypatch.setattr(ht._log, "info", spy_info)

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_full_chat_body())

    t = _make_transport(handler)
    t.request("POST", CHAT_PATH, json={"messages": []})

    assert len(calls) == 1
    kw = calls[0]
    assert kw["finish_reason"] == "stop"
    assert kw["content_len"] == 2
    assert kw["total_tokens"] == 11
    assert kw["prompt_tokens"] == 10
    assert kw["completion_tokens"] == 1
