"""`pais support-bundle` — one-shot zip of doctor + chat errors + log."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pais.cli.app import app as cli_app


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> CliRunner:
    """Isolate ~/.pais/logs under tmp_path for the duration of the test."""
    log_dir = tmp_path / ".pais" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "pais.log").write_text("sample log line\n", encoding="utf-8")

    from pais.cli import support_bundle_cmd

    monkeypatch.setenv("PAIS_MODE", "mock")
    monkeypatch.setenv("PAIS_LOG_FILE", str(log_dir / "pais.log"))
    monkeypatch.setenv("PAIS_LOG_LEVEL", "WARNING")
    monkeypatch.setattr(support_bundle_cmd, "_LOG_DIR", log_dir)
    return CliRunner()


def test_bundle_collects_existing_artifacts(runner: CliRunner, tmp_path: Path) -> None:
    """Without --chat, support-bundle zips doctor + chat-errors + pais.log from disk."""
    out = tmp_path / "bundle.zip"
    r = runner.invoke(cli_app, ["support-bundle", "-o", str(out)])
    assert r.exit_code == 0, r.output

    # Zip exists and contains doctor + log.
    assert out.exists()
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert any(n.startswith("doctor-") and n.endswith(".md") for n in names)
    assert "pais.log" in names

    # Output contains the bundle manifest JSON somewhere (after the doctor report).
    assert str(out) in r.output
    assert '"chat_errors": 0' in r.output
    assert '"mode": "mock"' in r.output


def test_bundle_includes_existing_chat_errors(
    runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-existing chat-error JSON files get bundled under chat-errors/."""
    from pais.cli import support_bundle_cmd

    ce_dir = support_bundle_cmd._LOG_DIR / "chat-errors"
    ce_dir.mkdir(parents=True, exist_ok=True)
    (ce_dir / "20260418T000000Z-rid-xyz.json").write_text(
        json.dumps({"request_id": "rid-xyz", "status_code": 502}),
        encoding="utf-8",
    )

    out = tmp_path / "bundle.zip"
    r = runner.invoke(cli_app, ["support-bundle", "-o", str(out)])
    assert r.exit_code == 0, r.output

    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert "chat-errors/20260418T000000Z-rid-xyz.json" in names


def test_bundle_requires_file_with_chat(runner: CliRunner, tmp_path: Path) -> None:
    """--chat without --file errors out (typer.BadParameter → exit code 2)."""
    r = runner.invoke(cli_app, ["support-bundle", "--chat", "agent_1"])
    assert r.exit_code == 2  # typer's parse-error code
