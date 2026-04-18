"""Tests for `pais agent dump` + `pais agent diagnose` commands (v0.8.2).

Uses a live uvicorn mock server so resources persist across CLI invocations.
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
from pais_mock.server import build_app
from pais_mock.state import Store


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


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
def mock_runner(monkeypatch: pytest.MonkeyPatch) -> Iterator[CliRunner]:
    """Live mock server so created agents persist across runner.invoke calls."""
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
    monkeypatch.setenv("PAIS_MODE", "http")
    monkeypatch.setenv("PAIS_BASE_URL", f"http://{host}:{port}/api/v1")
    monkeypatch.setenv("PAIS_AUTH", "none")
    monkeypatch.setenv("PAIS_LOG_LEVEL", "WARNING")
    try:
        yield CliRunner()
    finally:
        thread.stop()
        thread.join(timeout=2.0)


def _create_agent_minimal(runner: CliRunner) -> str:
    """Create an agent with NO recipe fields set; return its id."""
    r = runner.invoke(
        cli_app,
        [
            "agent",
            "create",
            "--name",
            "diag-min",
            "--model",
            "openai/gpt-oss-120b-4x",
            "--index-id",
            "idx_1",
            "--output",
            "json",
        ],
    )
    assert r.exit_code == 0, r.output
    return str(json.loads(r.output)["id"])


def _create_agent_field_proven(runner: CliRunner) -> str:
    r = runner.invoke(
        cli_app,
        [
            "agent",
            "create",
            "--name",
            "diag-fp",
            "--model",
            "openai/gpt-oss-120b-4x",
            "--template",
            "field-proven",
            "--index-id",
            "idx_1",
            "--output",
            "json",
        ],
    )
    assert r.exit_code == 0, r.output
    return str(json.loads(r.output)["id"])


def test_agent_dump_outputs_json_with_diagnostic_block(mock_runner: CliRunner) -> None:
    agent_id = _create_agent_minimal(mock_runner)
    r = mock_runner.invoke(cli_app, ["agent", "dump", agent_id])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["id"] == agent_id
    assert "__diagnostic" in data
    diag = data["__diagnostic"]
    # Minimal agent should be missing the 5 recommended fields.
    missing = set(diag["missing_recommended_fields"])
    assert "completion_role" in missing
    assert "chat_system_instruction_mode" in missing
    assert "index_reference_format" in missing
    assert "session_summarization_strategy" in missing


def test_agent_diagnose_minimal_exits_1(mock_runner: CliRunner) -> None:
    agent_id = _create_agent_minimal(mock_runner)
    r = mock_runner.invoke(cli_app, ["agent", "diagnose", agent_id])
    assert r.exit_code == 1, r.output
    assert "[MISS]" in r.output
    assert "chat_system_instruction_mode" in r.output
    assert "Fix:" in r.output


def test_agent_diagnose_field_proven_exits_0(mock_runner: CliRunner) -> None:
    agent_id = _create_agent_field_proven(mock_runner)
    r = mock_runner.invoke(cli_app, ["agent", "diagnose", agent_id])
    assert r.exit_code == 0, r.output
    assert "[MISS]" not in r.output
    assert "[ OK ]" in r.output


def test_agent_diagnose_warns_on_huge_session_max_length(
    mock_runner: CliRunner,
) -> None:
    """100K session_max_length triggers a WARN (history budget, not context window)."""
    r = mock_runner.invoke(
        cli_app,
        [
            "agent",
            "create",
            "--name",
            "huge",
            "--model",
            "openai/gpt-oss-120b-4x",
            "--template",
            "field-proven",
            "--session-max-length",
            "100000",
            "--index-id",
            "idx_1",
            "--output",
            "json",
        ],
    )
    assert r.exit_code == 0, r.output
    agent_id = json.loads(r.output)["id"]
    r2 = mock_runner.invoke(cli_app, ["agent", "diagnose", agent_id])
    assert "[WARN] session_max_length = 100000" in r2.output
