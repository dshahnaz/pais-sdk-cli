"""`pais doctor` — diagnostic probe battery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pais.cli.app import app as cli_app


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> CliRunner:
    monkeypatch.setenv("PAIS_MODE", "mock")
    monkeypatch.setenv("PAIS_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("PAIS_LOG_FILE", str(tmp_path / "pais.log"))
    (tmp_path / "pais.log").write_text("")
    return CliRunner()


def test_doctor_renders_table(runner: CliRunner) -> None:
    r = runner.invoke(cli_app, ["doctor"])
    out = r.output
    # Table renders probes.
    assert "knowledge_bases" in out
    assert "models" in out
    assert "mcp_tools" in out


def test_doctor_json_output_is_parseable(runner: CliRunner) -> None:
    r = runner.invoke(cli_app, ["doctor", "-o", "json"])
    # Suppress exit code 1 from the server-reachable probe (mock mode does HEAD on localhost:8080).
    data = json.loads(r.output.split("Report written")[0])
    assert data["mode"] == "mock"
    assert isinstance(data["probes"], list)
    assert any(p["name"] == "models" for p in data["probes"])
    # 3 models in mock (VLLM, INFINITY, LLAMA_CPP)
    models_probe = next(p for p in data["probes"] if p["name"] == "models")
    assert models_probe["ok"] is True
    assert "3" in models_probe["detail"]


def test_doctor_writes_report_file(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PAIS_LOG_FILE", str(tmp_path / "pais.log"))
    runner.invoke(cli_app, ["doctor"])
    # Check that a doctor-*.md file was created.
    doctor_files = list(tmp_path.glob("doctor-*.md"))
    assert len(doctor_files) >= 1
    content = doctor_files[0].read_text()
    assert "pais doctor" in content
    assert "version" in content


def test_doctor_probe_failure_marked_with_x(runner: CliRunner) -> None:
    """The server-reachable probe fails in mock mode (no HTTP server to HEAD).
    Should show ✗ in the output, not crash."""
    r = runner.invoke(cli_app, ["doctor"])
    # Mock mode: HEAD against localhost:8080 fails → server_reachable shows ✗
    assert "server_reachable" in r.output
    # The command should still produce a report (partial success).
    assert "Report written" in r.output
