"""`pais status` — full env overview command."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pais.cli import _alias
from pais.cli.app import app as cli_app


@pytest.fixture
def mock_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> CliRunner:
    monkeypatch.setenv("PAIS_MODE", "mock")
    monkeypatch.setenv("PAIS_LOG_LEVEL", "WARNING")
    monkeypatch.setattr(_alias, "CACHE_PATH", tmp_path / "aliases.json")
    return CliRunner()


def test_status_table_renders_all_sections(mock_runner: CliRunner) -> None:
    r = mock_runner.invoke(cli_app, ["status", "--no-ping"])
    assert r.exit_code == 0, r.output
    out = r.output
    for marker in ("Profile", "Mode", "Base URL", "Auth", "Verify SSL", "Server", "Alias cache"):
        assert marker in out
    # All resource sections render unconditionally — empty ones show "(none)".
    assert "Knowledge bases" in out
    assert "Indexes" in out
    assert "Agents" in out
    assert "Drift" in out


def test_status_json_output_is_parseable(mock_runner: CliRunner) -> None:
    r = mock_runner.invoke(cli_app, ["status", "--no-ping", "-o", "json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    for key in (
        "profile",
        "server",
        "alias_cache",
        "knowledge_bases",
        "indexes",
        "agents",
        "drift",
    ):
        assert key in payload
    assert payload["profile"]["mode"] == "mock"
    # mock mode never pings; server section must reflect that.
    assert payload["server"]["skipped"] is True
    # Empty server returns empty lists for resource sections (not missing keys).
    assert isinstance(payload["agents"], list)


def test_status_no_ping_skips_health(mock_runner: CliRunner) -> None:
    """`--no-ping` must produce a skipped server section even in http-shaped configs."""
    r = mock_runner.invoke(cli_app, ["status", "--no-ping", "-o", "json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["server"]["skipped"] is True


def test_status_with_counts_includes_documents_field(mock_runner: CliRunner) -> None:
    """`-c` adds the `documents` aggregate to KB/index rows."""
    r = mock_runner.invoke(cli_app, ["status", "--no-ping", "-c", "-o", "json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    # `indexes_count` is always present (always-paid round trip); `documents` only with -c.
    for row in payload["knowledge_bases"]:
        assert "indexes_count" in row
        assert "documents" in row
    for row in payload["indexes"]:
        assert "documents" in row


def test_status_without_counts_omits_documents_but_keeps_indexes(
    mock_runner: CliRunner,
) -> None:
    """Indexes section is populated even without `-c`; only `documents` is gated."""
    r = mock_runner.invoke(cli_app, ["status", "--no-ping", "-o", "json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    # The list itself is always present (and always populated when KBs exist).
    assert isinstance(payload["indexes"], list)
    for row in payload["knowledge_bases"]:
        # `indexes_count` is always present.
        assert "indexes_count" in row
        # `documents` is gated by -c.
        assert "documents" not in row
    for row in payload["indexes"]:
        assert "documents" not in row


def test_status_lists_indexes_and_agents_when_present(
    mock_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Use a live HTTP mock so KB/index/agent state persists across invocations."""
    import socket
    import threading
    import time as _t

    import uvicorn

    from pais_mock.server import build_app
    from pais_mock.state import Store

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    store = Store()
    app = build_app(store)

    class _T(threading.Thread):
        def __init__(self) -> None:
            super().__init__(daemon=True)
            self.server = uvicorn.Server(
                uvicorn.Config(app=app, host="127.0.0.1", port=port, log_level="warning")
            )

        def run(self) -> None:
            self.server.run()

        def stop(self) -> None:
            self.server.should_exit = True

    t = _T()
    t.start()
    deadline = _t.time() + 5.0
    while _t.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                break
        except OSError:
            _t.sleep(0.02)

    monkeypatch.setenv("PAIS_MODE", "http")
    monkeypatch.setenv("PAIS_BASE_URL", f"http://127.0.0.1:{port}/api/v1")
    monkeypatch.setenv("PAIS_AUTH", "none")
    runner = CliRunner()

    try:
        r = runner.invoke(cli_app, ["kb", "create", "--name", "kb1", "-o", "json"])
        assert r.exit_code == 0, r.output
        kb_id = json.loads(r.output)["id"]
        r = runner.invoke(
            cli_app,
            [
                "index",
                "create",
                kb_id,
                "--name",
                "ix1",
                "--embeddings-model",
                "BAAI/bge-small-en-v1.5",
                "-o",
                "json",
            ],
        )
        assert r.exit_code == 0, r.output
        r = runner.invoke(
            cli_app,
            [
                "agent",
                "create",
                "--name",
                "agent1",
                "--model",
                "openai/gpt-oss-120b-4x",
                "-o",
                "json",
            ],
        )
        assert r.exit_code == 0, r.output

        r = runner.invoke(cli_app, ["status", "--no-ping", "-o", "json"])
        assert r.exit_code == 0, r.output
        payload = json.loads(r.output)
        assert len(payload["knowledge_bases"]) == 1
        assert len(payload["indexes"]) == 1
        assert payload["indexes"][0]["name"] == "ix1"
        assert len(payload["agents"]) == 1
        assert payload["agents"][0]["name"] == "agent1"
    finally:
        t.stop()
        t.join(timeout=2.0)


def test_status_drift_reports_missing_kb_from_toml(
    mock_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A KB declared in TOML but not on the server shows up as `would-create` drift."""
    cfg = tmp_path / "pais.toml"
    cfg.write_text(
        textwrap.dedent(
            """
            [profiles.default]
            mode = "mock"

            [profiles.default.knowledge_bases.demo]
            name = "demo-kb"

              [[profiles.default.knowledge_bases.demo.indexes]]
              alias = "main"
              name = "demo-idx"
              embeddings_model_endpoint = "BAAI/bge-small-en-v1.5"
              chunk_size = 512
              chunk_overlap = 64

                [profiles.default.knowledge_bases.demo.indexes.splitter]
                kind = "test_suite_bge"
            """
        )
    )
    monkeypatch.setenv("PAIS_CONFIG", str(cfg))

    r = mock_runner.invoke(cli_app, ["status", "--no-ping", "-o", "json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    actions = {row["action"] for row in payload["drift"]}
    assert "would-create" in actions
