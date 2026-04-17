"""`pais logs path / tail / clear` smoke tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from pais.cli.app import app as cli_app


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setenv("PAIS_MODE", "mock")
    monkeypatch.setenv("PAIS_LOG_LEVEL", "WARNING")
    return CliRunner()


def test_logs_path_prints_configured_path(runner: CliRunner) -> None:
    r = runner.invoke(cli_app, ["logs", "path"])
    assert r.exit_code == 0, r.output
    assert "pais.log" in r.output


def test_logs_tail_reads_last_lines(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "test.log"
    log.write_text("\n".join(f"line {i}" for i in range(100)))
    monkeypatch.setenv("PAIS_LOG_FILE", str(log))
    r = runner.invoke(cli_app, ["logs", "tail", "-n", "5"])
    assert r.exit_code == 0, r.output
    lines = r.output.strip().splitlines()
    assert len(lines) == 5
    assert "line 99" in lines[-1]


def test_logs_clear_requires_yes(runner: CliRunner) -> None:
    r = runner.invoke(cli_app, ["logs", "clear"])
    assert r.exit_code == 1


def test_logs_clear_with_yes_truncates(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "test.log"
    log.write_text("some log data")
    monkeypatch.setenv("PAIS_LOG_FILE", str(log))
    r = runner.invoke(cli_app, ["logs", "clear", "--yes"])
    assert r.exit_code == 0, r.output
    assert log.read_text() == ""
