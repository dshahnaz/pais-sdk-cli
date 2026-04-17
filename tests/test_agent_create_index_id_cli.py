"""End-to-end: `pais agent create --index-id ... --index-top-n ... --index-similarity-cutoff ...`
round-trips through the live mock server (v0.7.1)."""

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
    port = s.getsockname()[1]
    s.close()
    return port


class _ServerThread(threading.Thread):
    def __init__(self, app: object, host: str, port: int) -> None:
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


def test_agent_create_with_index_flags(live_server: str) -> None:
    """The doc-aligned shape reaches the server and round-trips."""
    runner = CliRunner()

    r = runner.invoke(cli_app, ["kb", "create", "--name", "for-agent", "--output", "json"])
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

    r = runner.invoke(
        cli_app,
        [
            "agent",
            "create",
            "--name",
            "doc-agent",
            "--model",
            "openai/gpt-oss-120b-4x",
            "--index-id",
            ix_id,
            "--index-top-n",
            "3",
            "--index-similarity-cutoff",
            "0.42",
            "--output",
            "json",
        ],
    )
    assert r.exit_code == 0, r.output
    agent = json.loads(r.output)
    assert agent["name"] == "doc-agent"
    assert agent["index_id"] == ix_id
    assert agent["index_top_n"] == 3
    assert agent["index_similarity_cutoff"] == 0.42
    # Doc-aligned path leaves legacy `tools` empty.
    assert agent["tools"] == []


def test_agent_create_without_index_omits_index_fields(live_server: str) -> None:
    """No --index-id → index_* fields are None (server-side defaults apply)."""
    runner = CliRunner()
    r = runner.invoke(
        cli_app,
        [
            "agent",
            "create",
            "--name",
            "bare-agent",
            "--model",
            "openai/gpt-oss-120b-4x",
            "--output",
            "json",
        ],
    )
    assert r.exit_code == 0, r.output
    agent = json.loads(r.output)
    # None-valued index fields may be omitted by the server on the wire.
    assert agent.get("index_id") is None
    assert agent.get("index_top_n") is None
    assert agent.get("index_similarity_cutoff") is None


def test_legacy_kb_search_tool_flag_still_wires_a_toollink(live_server: str) -> None:
    """`--kb-search-tool` is hidden but still functional for scripted back-compat."""
    runner = CliRunner()
    r = runner.invoke(
        cli_app,
        [
            "agent",
            "create",
            "--name",
            "legacy-agent",
            "--model",
            "openai/gpt-oss-120b-4x",
            "--kb-search-tool",
            "some-mcp-uuid",
            "--output",
            "json",
        ],
    )
    assert r.exit_code == 0, r.output
    agent = json.loads(r.output)
    assert len(agent["tools"]) == 1
    assert agent["tools"][0]["tool_id"] == "some-mcp-uuid"
