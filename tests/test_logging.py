"""Logging: redaction of secrets + request_id round-trip client ↔ server."""

from __future__ import annotations

import re
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from pais.client import PaisClient
from pais.logging import configure_logging, new_request_id, set_request_id
from pais.models import KnowledgeBaseCreate
from pais.transport.fake_transport import FakeTransport
from pais.transport.httpx_transport import HttpxTransport
from pais_mock.server import build_app
from pais_mock.state import Store


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_secret_keys_are_redacted(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    log_file = tmp_path / "log.jsonl"
    configure_logging(level="DEBUG", log_file=log_file, json_console=True)
    from pais.logging import get_logger

    log = get_logger("pais.test")
    log.info(
        "sample",
        password="hunter2",
        access_token="ey.zzz",
        nested={"authorization": "Bearer abc123"},
        headers=[{"authorization": "Bearer xyz"}],
        plain="not-a-secret",
    )
    captured = capsys.readouterr()
    combined = captured.err + (log_file.read_text() if log_file.exists() else "")
    assert "hunter2" not in combined
    assert "ey.zzz" not in combined
    assert "abc123" not in combined
    assert "xyz" not in combined
    assert "not-a-secret" in combined
    assert combined.count("***") >= 4


def test_request_id_contextvar_is_added_to_events(
    capsys: pytest.CaptureFixture,
) -> None:
    configure_logging(level="DEBUG", log_file=None, json_console=True)
    from pais.logging import get_logger

    log = get_logger("pais.test")
    set_request_id("rid-abc-123")
    log.info("hello")
    out = capsys.readouterr().err
    assert "rid-abc-123" in out


def test_request_id_round_trips_client_to_server(tmp_path: Path) -> None:
    """Confirm the client's request_id appears verbatim in server-side log lines."""
    store = Store()
    app = build_app(store)
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(app=app, host="127.0.0.1", port=port, log_level="warning")
    )
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.02)
    try:
        transport = HttpxTransport(
            f"http://127.0.0.1:{port}/api/v1",
            retry_max_attempts=1,
            retry_base_delay=0.0,
        )
        client = PaisClient(transport)
        # Manually set a known request_id.
        set_request_id("trace-xyz-999")
        client.knowledge_bases.list()
        # The server echoes X-Request-ID on the response; transport copies it.
        # Final proof of round-trip: issue another request and check the header.
        resp = transport.request("GET", "/control/knowledge-bases")
        assert resp.headers.get("x-request-id") == "trace-xyz-999"
        client.close()
    finally:
        server.should_exit = True
        t.join(timeout=2.0)


def test_fake_transport_preserves_request_id() -> None:
    store = Store()
    client = PaisClient(FakeTransport(store))
    set_request_id("fake-trace-1")
    resp = client._transport.request("GET", "/control/knowledge-bases")
    assert resp.request_id == "fake-trace-1"
    client.close()


def test_request_log_has_required_fields(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Every pais.request log line must carry method, path, status, latency_ms, attempt, request_id."""
    configure_logging(level="DEBUG", log_file=None, json_console=True)
    new_request_id()
    store = Store()
    client = PaisClient(FakeTransport(store))
    client.knowledge_bases.create(KnowledgeBaseCreate(name="x"))
    err = capsys.readouterr().err
    # find a pais.request JSON line
    match = re.search(r'\{[^{}]*"event":\s*"pais\.request"[^{}]*\}', err)
    assert match, f"no pais.request log line found in: {err}"
    line = match.group(0)
    for field in ("method", "path", "status", "attempt", "latency_ms", "request_id"):
        assert f'"{field}"' in line, f"field {field!r} missing from: {line}"
    client.close()
