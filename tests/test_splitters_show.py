"""`pais splitters show <kind>` and `splitters list -v` render meta correctly."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from pais.cli.app import app as cli_app
from pais.ingest.registry import SPLITTER_REGISTRY


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setenv("PAIS_MODE", "mock")
    monkeypatch.setenv("PAIS_LOG_LEVEL", "WARNING")
    return CliRunner()


@pytest.mark.parametrize("kind", sorted(SPLITTER_REGISTRY))
def test_show_table_includes_summary_and_algorithm(runner: CliRunner, kind: str) -> None:
    r = runner.invoke(cli_app, ["splitters", "show", kind])
    assert r.exit_code == 0, r.output
    out = r.output
    assert kind in out
    # Section headers appear in rich panel
    assert "Input" in out
    assert "Algorithm" in out
    assert "Output" in out


@pytest.mark.parametrize("kind", sorted(SPLITTER_REGISTRY))
def test_show_json_output_returns_meta_dict(runner: CliRunner, kind: str) -> None:
    r = runner.invoke(cli_app, ["splitters", "show", kind, "-o", "json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["kind"] == kind
    assert "meta" in data
    meta = data["meta"]
    for key in (
        "summary",
        "input_type",
        "algorithm",
        "chunk_size_unit",
        "typical_chunk_size",
        "example_input",
    ):
        assert key in meta
        assert meta[key], f"{kind}: meta.{key} is empty in JSON output"


@pytest.mark.parametrize("kind", ["test_suite_bge", "test_suite_arctic"])
def test_show_renders_recommended_index_config(runner: CliRunner, kind: str) -> None:
    """v0.7.0: splitters with target_embeddings_model / suggested_index_chunk_size
    render a 'Recommended index config' footer so users know what IndexCreate body to pass."""
    r = runner.invoke(cli_app, ["splitters", "show", kind])
    assert r.exit_code == 0, r.output
    assert "Recommended index config" in r.output
    assert "embeddings_model_endpoint" in r.output
    assert "chunk_size" in r.output


def test_list_default_compact(runner: CliRunner) -> None:
    """Default columns: kind + summary."""
    r = runner.invoke(cli_app, ["splitters", "list"])
    assert r.exit_code == 0, r.output
    assert "kind" in r.output
    assert "summary" in r.output


def test_list_verbose_adds_input_and_chunk_size(runner: CliRunner) -> None:
    r = runner.invoke(cli_app, ["splitters", "list", "-v"])
    assert r.exit_code == 0, r.output
    assert "input" in r.output
    assert "chunk_size" in r.output


def test_list_json_output_returns_all_kinds(runner: CliRunner) -> None:
    r = runner.invoke(cli_app, ["splitters", "list", "-v", "-o", "json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    kinds = {row["kind"] for row in data}
    assert kinds == set(SPLITTER_REGISTRY)
