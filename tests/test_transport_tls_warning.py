"""v0.6.7 regression: the `pais.tls.verification_disabled` warning fires
exactly once per base URL per process, not once per `HttpxTransport`
construction. In an interactive session, multiple transports are created
(one at the landing screen, one per dispatch), and the user complained
that the same advisory line was spamming their terminal.

structlog routes through its own stderr factory — stdlib caplog can't see
those records. We assert against the module-level dedup set and against
captured stderr where needed."""

from __future__ import annotations

import pytest

from pais.transport import httpx_transport as tx_mod


@pytest.fixture(autouse=True)
def _reset_warned_set() -> None:
    """Ensure a clean slate between tests — the dedup set persists per process."""
    tx_mod._warned_tls_off.clear()
    yield
    tx_mod._warned_tls_off.clear()


def test_warning_tracks_base_url_in_dedup_set() -> None:
    """Same base URL → one entry added, regardless of how many transports."""
    tx_mod.HttpxTransport("https://example.invalid/api/v1", verify_ssl=False)
    assert tx_mod._warned_tls_off == {"https://example.invalid/api/v1"}

    tx_mod.HttpxTransport("https://example.invalid/api/v1", verify_ssl=False)
    # Set size unchanged — dedup holding.
    assert tx_mod._warned_tls_off == {"https://example.invalid/api/v1"}


def test_warning_tracks_each_unique_base_url() -> None:
    tx_mod.HttpxTransport("https://host-a.invalid/api/v1", verify_ssl=False)
    tx_mod.HttpxTransport("https://host-b.invalid/api/v1", verify_ssl=False)
    assert tx_mod._warned_tls_off == {
        "https://host-a.invalid/api/v1",
        "https://host-b.invalid/api/v1",
    }


def test_warning_not_recorded_when_verify_ssl_is_true() -> None:
    tx_mod.HttpxTransport("https://example.invalid/api/v1", verify_ssl=True)
    assert tx_mod._warned_tls_off == set()


def test_warning_only_logs_once_via_logger_spy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end check via the module's own logger — intercept warnings
    before structlog serializes them so we can count invocations directly."""
    calls: list[tuple[str, tuple, dict]] = []

    class _SpyLogger:
        def warning(self, event: str, *args: object, **kw: object) -> None:
            calls.append((event, args, kw))

        # structlog BoundLogger surface — stubs for anything else it calls.
        def __getattr__(self, name: str):
            return lambda *a, **kw: None

    monkeypatch.setattr(tx_mod, "_log", _SpyLogger())

    tx_mod.HttpxTransport("https://example.invalid/api/v1", verify_ssl=False)
    tx_mod.HttpxTransport("https://example.invalid/api/v1", verify_ssl=False)
    tx_mod.HttpxTransport("https://example.invalid/api/v1", verify_ssl=False)

    warn_events = [c[0] for c in calls if c[0] == "pais.tls.verification_disabled"]
    assert len(warn_events) == 1, warn_events
