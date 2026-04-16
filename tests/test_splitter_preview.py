"""`preview()` and `pais splitters preview` — chunk distribution in tokens + chars."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pais.cli._splitter_preview import preview
from pais.cli.app import app as cli_app


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setenv("PAIS_MODE", "mock")
    monkeypatch.setenv("PAIS_LOG_LEVEL", "WARNING")
    return CliRunner()


@pytest.fixture
def text_fixture(tmp_path: Path) -> Path:
    """A predictable text file for preview to chew on."""
    p = tmp_path / "sample.txt"
    p.write_text("alpha beta gamma delta epsilon. " * 200)  # ~6200 chars
    return p


def test_preview_text_chunks_returns_expected_chunk_count(text_fixture: Path) -> None:
    """text_chunks(default 1500/100) over a ~6200-char file → ~5 chunks."""
    report = preview("text_chunks", text_fixture)
    # Defaults: chunk_chars=1500, overlap=100 → step=1400, ceil(6200/1400) ≈ 5
    assert report.files_scanned == 1
    assert 4 <= report.chunks_emitted <= 6
    assert report.char_stats.median > 0


def test_preview_provides_token_distribution_when_tokenizers_installed(
    text_fixture: Path,
) -> None:
    """token_stats and the median ratio populate when `tokenizers` is available."""
    report = preview("text_chunks", text_fixture)
    if report.token_stats is None:
        pytest.skip("tokenizers not installed in this env")
    assert report.token_stats.median > 0
    assert report.char_token_ratio_median is not None
    # English text under bge-small ≈ 3-5 chars/token.
    assert 2.0 <= report.char_token_ratio_median <= 6.0


def test_preview_falls_back_to_char_only_when_tokenizers_missing(
    text_fixture: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Force the import to fail and confirm the report still completes."""
    import sys

    # Make the token_budget import raise.
    monkeypatch.setitem(sys.modules, "pais.dev.token_budget", None)
    report = preview("text_chunks", text_fixture)
    assert report.token_stats is None
    assert report.char_token_ratio_median is None
    assert any("token counts unavailable" in n for n in report.notes)
    # Char stats still populated — the splitter ran fine.
    assert report.chunks_emitted > 0


def test_preview_includes_sample_chunk(text_fixture: Path) -> None:
    report = preview("text_chunks", text_fixture)
    assert report.sample_chunk_first300
    assert "alpha" in report.sample_chunk_first300


def test_preview_directory_walk(tmp_path: Path) -> None:
    """A directory with multiple files gets multi-file scanning."""
    for i in range(3):
        (tmp_path / f"f{i}.txt").write_text("hello " * 200)
    report = preview("text_chunks", tmp_path)
    assert report.files_scanned == 3
    assert report.chunks_emitted >= 3


def test_preview_respects_limit(tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text("x" * 100)
    report = preview("text_chunks", tmp_path, limit=2)
    assert report.files_scanned == 2
    assert report.truncated is True


def test_preview_command_renders_table(runner: CliRunner, text_fixture: Path) -> None:
    r = runner.invoke(cli_app, ["splitters", "preview", "text_chunks", str(text_fixture)])
    assert r.exit_code == 0, r.output
    assert "Chunks emitted" in r.output
    assert "Char distribution" in r.output


def test_preview_command_json_output_is_parseable(runner: CliRunner, text_fixture: Path) -> None:
    r = runner.invoke(
        cli_app, ["splitters", "preview", "text_chunks", str(text_fixture), "-o", "json"]
    )
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["kind"] == "text_chunks"
    assert data["chunks_emitted"] > 0
    assert "char_stats" in data
