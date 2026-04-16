"""`pais-dev` CLI smoke tests."""

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

from pais.cli.dev import app as dev_app
from pais_mock.server import build_app
from pais_mock.state import Store

_SUITE = """# Demo-Suite

## Overview

Demo overview.

## Test Coverage

### testOne

Does one thing.

### testTwo

Does two things.

## Technology Stack

- Python
"""


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
    base = f"http://127.0.0.1:{port}/api/v1"
    monkeypatch.setenv("PAIS_MODE", "http")
    monkeypatch.setenv("PAIS_BASE_URL", base)
    monkeypatch.setenv("PAIS_AUTH", "none")
    monkeypatch.setenv("PAIS_LOG_LEVEL", "WARNING")
    try:
        yield base
    finally:
        t.stop()
        t.join(timeout=2.0)


def test_dev_version_flag() -> None:
    from pais import __version__

    runner = CliRunner()
    r = runner.invoke(dev_app, ["--version"])
    assert r.exit_code == 0, r.output
    assert __version__ in r.output


def test_split_suite_cmd(tmp_path: Path) -> None:
    runner = CliRunner()
    src = tmp_path / "Demo.md"
    src.write_text(_SUITE)
    out = tmp_path / "out"
    r = runner.invoke(dev_app, ["split-suite", str(src), "--out", str(out), "--output", "json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["suite"] == "Demo-Suite"
    assert data["sections_emitted"] >= 3
    assert all(Path(f).exists() for f in data["files"])


def test_ingest_suite_cmd_against_live_mock(live_server: str, tmp_path: Path) -> None:
    # Create KB + index via the main CLI so pais-dev has real ids.
    from pais.cli.app import app as pais_app

    runner = CliRunner()
    r = runner.invoke(pais_app, ["kb", "create", "--name", "t", "--output", "json"])
    assert r.exit_code == 0, r.output
    kb_id = json.loads(r.output)["id"]
    r = runner.invoke(
        pais_app,
        [
            "index",
            "create",
            kb_id,
            "--name",
            "ix",
            "--embeddings-model",
            "BAAI/bge-small-en-v1.5",
            "--output",
            "json",
        ],
    )
    assert r.exit_code == 0, r.output
    ix_id = json.loads(r.output)["id"]

    src = tmp_path / "Demo.md"
    src.write_text(_SUITE)
    r = runner.invoke(
        dev_app,
        ["ingest-suite", str(src), "--kb", kb_id, "--index", ix_id, "--output", "json"],
    )
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["sections_emitted"] == data["sections_uploaded"] >= 3
    assert data["errors"] == []


def test_ingest_suites_cmd_writes_report(live_server: str, tmp_path: Path) -> None:
    from pais.cli.app import app as pais_app

    runner = CliRunner()
    r = runner.invoke(pais_app, ["kb", "create", "--name", "t", "--output", "json"])
    kb_id = json.loads(r.output)["id"]
    r = runner.invoke(
        pais_app,
        [
            "index",
            "create",
            kb_id,
            "--name",
            "ix",
            "--embeddings-model",
            "BAAI/bge-small-en-v1.5",
            "--output",
            "json",
        ],
    )
    ix_id = json.loads(r.output)["id"]

    suites_dir = tmp_path / "suites"
    suites_dir.mkdir()
    for n in ("A", "B"):
        (suites_dir / f"{n}.md").write_text(_SUITE.replace("Demo-Suite", f"{n}-Suite"))

    report_path = tmp_path / "report.json"
    r = runner.invoke(
        dev_app,
        [
            "ingest-suites",
            str(suites_dir),
            "--kb",
            kb_id,
            "--index",
            ix_id,
            "--workers",
            "2",
            "--report",
            str(report_path),
            "--output",
            "json",
        ],
    )
    assert r.exit_code == 0, r.output
    assert report_path.exists()
    parsed = json.loads(report_path.read_text())
    assert parsed["summary"]["total_suites"] == 2
    assert parsed["summary"]["total_suites_failed"] == 0
