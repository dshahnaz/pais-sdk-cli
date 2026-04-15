"""H7 gate: retry/timeout/cold-start behaviors via httpx.MockTransport."""

from __future__ import annotations

import time

import httpx
import pytest

from pais.errors import PaisRateLimitError, PaisTimeoutError
from pais.transport.httpx_transport import HttpxTransport


def _wire_mock(t: HttpxTransport, handler) -> None:
    mock = httpx.MockTransport(handler)
    t._client.close()
    t._client = httpx.Client(base_url=t.base_url, transport=mock, timeout=t._client.timeout)


def test_429_honors_retry_after_delay() -> None:
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"detail": "slow"}, headers={"Retry-After": "1"})
        return httpx.Response(200, json={})

    t = HttpxTransport("http://testserver/api/v1", retry_max_attempts=3, retry_base_delay=0.0)
    _wire_mock(t, handler)
    started = time.perf_counter()
    t.request("GET", "/kb")
    elapsed = time.perf_counter() - started
    assert elapsed >= 0.9  # waited ~1s as instructed
    assert calls["n"] == 2


def test_total_timeout_raises_timeout_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        # Force httpx to think connect timed out, so the transport retries.
        # With total_timeout set low, the retry loop eventually exits via
        # the total-deadline check and raises PaisTimeoutError.
        raise httpx.ConnectTimeout("slow")

    t = HttpxTransport(
        "http://testserver/api/v1",
        retry_max_attempts=10,
        retry_base_delay=0.05,
        total_timeout=0.1,
    )
    _wire_mock(t, handler)
    with pytest.raises(PaisTimeoutError):
        t.request("GET", "/kb")


def test_rate_limit_exhaustion_raises_rate_limit() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "nope"}, headers={"Retry-After": "0"})

    t = HttpxTransport("http://testserver/api/v1", retry_max_attempts=2, retry_base_delay=0.0)
    _wire_mock(t, handler)
    with pytest.raises(PaisRateLimitError):
        t.request("GET", "/kb")
