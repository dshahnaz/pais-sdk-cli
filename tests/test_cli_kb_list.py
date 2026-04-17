"""`kb list --with-counts` resilience test.

Pins the v0.6.8 behavior: one KB whose `/indexes` endpoint 422s must not
sink the whole listing. The offending row shows `!` markers and the server's
validation detail is surfaced on stderr so the user can see what the server
rejected.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn
from typer.testing import CliRunner

from pais.cli.app import app as cli_app
from pais.errors import ErrorDetail, PaisValidationError
from pais.resources.indexes import IndexesResource
from pais_mock.server import build_app
from pais_mock.state import Store


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _ServerThread(threading.Thread):
    def __init__(self, app, host: str, port: int) -> None:
        super().__init__(daemon=True)
        self.server = uvicorn.Server(
            uvicorn.Config(app=app, host=host, port=port, log_level="warning")
        )

    def run(self) -> None:
        self.server.run()

    def stop(self) -> None:
        self.server.should_exit = True


@pytest.fixture
def live_server(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    store = Store()
    app = build_app(store)
    host = "127.0.0.1"
    port = _free_port()
    thread = _ServerThread(app, host, port)
    thread.start()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.02)
    base = f"http://{host}:{port}/api/v1"
    monkeypatch.setenv("PAIS_MODE", "http")
    monkeypatch.setenv("PAIS_BASE_URL", base)
    monkeypatch.setenv("PAIS_AUTH", "none")
    monkeypatch.setenv("PAIS_LOG_LEVEL", "WARNING")
    try:
        yield base
    finally:
        thread.stop()
        thread.join(timeout=2.0)


def test_kb_list_with_counts_survives_one_bad_kb(
    live_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two KBs: one's `/indexes` raises 422 client-side; the other succeeds.
    Exit 0, both rows render (one with `!`), stderr carries the enriched
    `detail=…` so the user sees which field the server rejected."""
    runner = CliRunner()

    r = runner.invoke(cli_app, ["kb", "create", "--name", "kb-good"])
    assert r.exit_code == 0, r.stdout
    r = runner.invoke(cli_app, ["kb", "create", "--name", "kb-bad"])
    assert r.exit_code == 0, r.stdout

    r = runner.invoke(cli_app, ["kb", "list", "--output", "json"])
    assert r.exit_code == 0, r.stdout
    kbs = json.loads(r.stdout)
    bad_id = next(k for k in kbs if k["name"] == "kb-bad")["id"]

    original_list = IndexesResource.list

    def _flaky_list(self: IndexesResource, kb_id: str, **kw):  # type: ignore[no-untyped-def]
        if kb_id == bad_id:
            raise PaisValidationError(
                "PAIS request failed with status 422",
                status_code=422,
                request_id="req-flaky",
                details=[
                    ErrorDetail(
                        error_code="VALIDATION_ERROR",
                        loc=["query", "limit"],
                        msg="field required",
                    )
                ],
            )
        return original_list(self, kb_id, **kw)

    monkeypatch.setattr(IndexesResource, "list", _flaky_list)

    r = runner.invoke(cli_app, ["kb", "list", "--with-counts"])

    # Exit code stays 0 — one bad KB mustn't sink the whole command.
    assert r.exit_code == 0, (r.stdout, r.stderr)
    # Both rows render; the bad row has "!" markers.
    assert "kb-good" in r.stdout
    assert "kb-bad" in r.stdout
    assert "!" in r.stdout
    # The typer CliRunner mixes stdout+stderr into `.output` by default.
    combined = r.output
    assert "warn: kb=kb-bad" in combined
    assert "detail=query.limit: field required" in combined


def test_kb_list_without_with_counts_is_untouched(live_server: str) -> None:
    """Guard: bare `kb list` never touches `/indexes`, so the fix doesn't
    alter that code path."""
    runner = CliRunner()
    r = runner.invoke(cli_app, ["kb", "create", "--name", "kb-plain"])
    assert r.exit_code == 0
    r = runner.invoke(cli_app, ["kb", "list"])
    assert r.exit_code == 0
    assert "kb-plain" in r.stdout
    # Without --with-counts, no indexes/documents columns.
    assert "documents" not in r.stdout.lower()
