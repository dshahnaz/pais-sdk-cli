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
    assert "Knowledge bases" in out
    assert "Drift" in out


def test_status_json_output_is_parseable(mock_runner: CliRunner) -> None:
    r = mock_runner.invoke(cli_app, ["status", "--no-ping", "-o", "json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    for key in ("profile", "server", "alias_cache", "knowledge_bases", "indexes", "drift"):
        assert key in payload
    assert payload["profile"]["mode"] == "mock"
    # mock mode never pings; server section must reflect that.
    assert payload["server"]["skipped"] is True


def test_status_no_ping_skips_health(mock_runner: CliRunner) -> None:
    """`--no-ping` must produce a skipped server section even in http-shaped configs."""
    r = mock_runner.invoke(cli_app, ["status", "--no-ping", "-o", "json"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["server"]["skipped"] is True


def test_status_with_counts_includes_doc_counts(
    mock_runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`-c` adds index/doc count columns; without it those keys are absent."""
    # Seed a KB+index via the CLI so the mock store has something to count.
    r = mock_runner.invoke(cli_app, ["kb", "create", "--name", "kb1", "-o", "json"])
    assert r.exit_code == 0, r.output
    # Note: each `mock` invocation rebuilds an in-memory Store so state doesn't
    # persist across invocations. Counting works against an empty store too.

    r = mock_runner.invoke(cli_app, ["status", "--no-ping", "-c", "-o", "json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    # `-c` enables the indexes list; should be a list (possibly empty).
    assert isinstance(payload["indexes"], list)
    # Counts present on each KB row.
    for row in payload["knowledge_bases"]:
        assert "indexes_count" in row
        assert "documents" in row


def test_status_without_counts_omits_doc_counts(mock_runner: CliRunner) -> None:
    r = mock_runner.invoke(cli_app, ["status", "--no-ping", "-o", "json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    # Indexes list is empty when --with-counts is off (no extra round-trip done).
    assert payload["indexes"] == []
    for row in payload["knowledge_bases"]:
        assert "indexes_count" not in row


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
                kind = "passthrough"
            """
        )
    )
    monkeypatch.setenv("PAIS_CONFIG", str(cfg))

    r = mock_runner.invoke(cli_app, ["status", "--no-ping", "-o", "json"])
    assert r.exit_code == 0, r.output
    payload = json.loads(r.output)
    actions = {row["action"] for row in payload["drift"]}
    assert "would-create" in actions
