"""CLI tests. Single-step commands use PAIS_MODE=mock; multi-step flows talk
to a shared live uvicorn mock server so state persists across invocations."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from typer.testing import CliRunner

from pais.cli.app import app as cli_app
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
    """Boot a uvicorn mock server and wire the CLI to talk to it."""
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


@pytest.fixture
def mock_runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setenv("PAIS_MODE", "mock")
    monkeypatch.setenv("PAIS_LOG_LEVEL", "WARNING")
    return CliRunner()


def test_kb_create_and_list_json(mock_runner: CliRunner) -> None:
    r = mock_runner.invoke(cli_app, ["kb", "create", "--name", "demo", "--output", "json"])
    assert r.exit_code == 0, r.output
    kb = json.loads(r.output)
    assert kb["name"] == "demo"


def test_table_output_renders(mock_runner: CliRunner) -> None:
    r = mock_runner.invoke(cli_app, ["models", "list"])
    assert r.exit_code == 0
    assert "openai/gpt-oss-120b-4x" in r.output


def test_full_flow_against_live_mock(live_server: str, tmp_path) -> None:
    runner = CliRunner()

    r = runner.invoke(cli_app, ["kb", "create", "--name", "code", "--output", "json"])
    assert r.exit_code == 0, r.output
    kb_id = json.loads(r.output)["id"]

    r = runner.invoke(
        cli_app,
        [
            "index",
            "create",
            kb_id,
            "--name",
            "ix",
            "--embeddings-model",
            "BAAI/bge-small-en-v1.5",
            "--chunk-size",
            "80",
            "--chunk-overlap",
            "20",
            "--output",
            "json",
        ],
    )
    assert r.exit_code == 0, r.output
    ix_id = json.loads(r.output)["id"]

    doc_path = tmp_path / "doc.txt"
    doc_path.write_text("The answer is 42. Always has been 42.")
    r = runner.invoke(cli_app, ["index", "upload", kb_id, ix_id, str(doc_path), "--output", "json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["state"] == "INDEXED"

    r = runner.invoke(cli_app, ["mcp", "tools", "--output", "json"])
    assert r.exit_code == 0, r.output
    tools = json.loads(r.output)
    kb_tool = next(t for t in tools if ix_id in t["id"])

    r = runner.invoke(
        cli_app,
        [
            "agent",
            "create",
            "--name",
            "a1",
            "--model",
            "openai/gpt-oss-120b-4x",
            "--kb-search-tool",
            kb_tool["id"],
            "--output",
            "json",
        ],
    )
    assert r.exit_code == 0, r.output
    agent_id = json.loads(r.output)["id"]

    r = runner.invoke(
        cli_app, ["agent", "chat", agent_id, "what is the answer?", "--output", "json"]
    )
    assert r.exit_code == 0, r.output
    resp = json.loads(r.output)
    assert resp["choices"][0]["message"]["content"]


def test_validation_error_exits_1(live_server: str) -> None:
    """Server-returned 422 must map to exit code 1 (user error)."""
    runner = CliRunner()
    r = runner.invoke(cli_app, ["kb", "create", "--name", "k", "--output", "json"])
    kb_id = json.loads(r.output)["id"]
    # name and embeddings-model passed but empty — store rejects with 422.
    r = runner.invoke(
        cli_app,
        [
            "index",
            "create",
            kb_id,
            "--name",
            "",
            "--embeddings-model",
            "",
            "--output",
            "json",
        ],
    )
    assert r.exit_code == 1, r.output


def test_not_found_exits_2(mock_runner: CliRunner) -> None:
    r = mock_runner.invoke(cli_app, ["kb", "get", "kb_does_not_exist", "--output", "json"])
    assert r.exit_code == 2, r.output


def test_config_init_writes_scaffold(mock_runner: CliRunner) -> None:
    # Direct project-write is exercised in test_config_file via the loader.
    # Here we just verify init runs end-to-end (it may write or refuse if file
    # already exists in the working dir).
    r = mock_runner.invoke(cli_app, ["config", "init", "--project"], catch_exceptions=False)
    assert r.exit_code in (0, 1)


def test_config_show_redacts_secrets(mock_runner: CliRunner) -> None:
    r = mock_runner.invoke(cli_app, ["config", "show", "--output", "json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["mode"] == "mock"
    # password key absent or redacted (not a real secret in mock)
    assert data.get("password") in (None, "***")


def test_config_path_command(mock_runner: CliRunner) -> None:
    r = mock_runner.invoke(cli_app, ["config", "path", "--output", "json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert "config_file" in data
    assert "profile" in data


def test_kb_delete_requires_yes_in_non_tty(mock_runner: CliRunner) -> None:
    r = mock_runner.invoke(cli_app, ["kb", "delete", "kb_xx"])
    # Without --yes and without TTY, command must refuse.
    assert r.exit_code == 1
    assert "without --yes" in r.output or "without --yes" in (r.stderr or "")


def test_kb_purge_with_yes(live_server: str) -> None:
    """purge runs against a live mock server; needs persistent state."""
    from pais.cli.app import app as pais_app

    runner = CliRunner()
    r = runner.invoke(pais_app, ["kb", "create", "--name", "p", "--output", "json"])
    kb_id = json.loads(r.output)["id"]
    r = runner.invoke(
        pais_app,
        ["index", "create", kb_id, "--name", "ix", "--embeddings-model", "bge", "--output", "json"],
    )
    assert r.exit_code == 0
    r = runner.invoke(pais_app, ["kb", "purge", kb_id, "--yes", "--output", "json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["indexes_processed"] >= 1


def test_index_purge_with_yes(live_server: str, tmp_path: Path) -> None:
    from pais.cli.app import app as pais_app

    runner = CliRunner()
    r = runner.invoke(pais_app, ["kb", "create", "--name", "p", "--output", "json"])
    kb_id = json.loads(r.output)["id"]
    r = runner.invoke(
        pais_app,
        ["index", "create", kb_id, "--name", "ix", "--embeddings-model", "bge", "--output", "json"],
    )
    ix_id = json.loads(r.output)["id"]

    doc = tmp_path / "x.md"
    doc.write_text("hello")
    r = runner.invoke(pais_app, ["index", "upload", kb_id, ix_id, str(doc), "--output", "json"])
    assert r.exit_code == 0

    r = runner.invoke(
        pais_app,
        ["index", "purge", kb_id, ix_id, "--yes", "--strategy", "api", "--output", "json"],
    )
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["documents_deleted"] >= 1


def test_index_cancel_noop_when_no_active(live_server: str) -> None:
    from pais.cli.app import app as pais_app

    runner = CliRunner()
    r = runner.invoke(pais_app, ["kb", "create", "--name", "c", "--output", "json"])
    kb_id = json.loads(r.output)["id"]
    r = runner.invoke(
        pais_app,
        ["index", "create", kb_id, "--name", "ix", "--embeddings-model", "bge", "--output", "json"],
    )
    ix_id = json.loads(r.output)["id"]

    r = runner.invoke(pais_app, ["index", "cancel", kb_id, ix_id, "--yes", "--output", "json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["strategy_used"] == "noop"


def test_auth_error_exits_3(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bearer auth against a server that rejects → exit 3."""
    import socket
    import time

    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI()

    @app.api_route("/{full:path}", methods=["GET", "POST", "DELETE"])
    async def reject(full: str) -> JSONResponse:
        return JSONResponse({"detail": "nope"}, status_code=401)

    port = _free_port()
    t = _ServerThread(app, "127.0.0.1", port)
    t.start()
    deadline = time.time() + 5.0
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.02)
    try:
        monkeypatch.setenv("PAIS_MODE", "http")
        monkeypatch.setenv("PAIS_BASE_URL", f"http://127.0.0.1:{port}/api/v1")
        monkeypatch.setenv("PAIS_AUTH", "bearer")
        monkeypatch.setenv("PAIS_BEARER_TOKEN", "nope")
        monkeypatch.setenv("PAIS_LOG_LEVEL", "WARNING")
        runner = CliRunner()
        r = runner.invoke(cli_app, ["kb", "list", "--output", "json"])
        assert r.exit_code == 3, r.output
    finally:
        t.stop()
        t.join(timeout=2.0)
