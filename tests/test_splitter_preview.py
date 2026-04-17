"""`preview()` and `pais splitters preview` — chunk distribution + --dump + --show-all."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pais.cli._splitter_preview import preview
from pais.cli.app import app as cli_app

FIXTURE = Path(__file__).parent / "fixtures" / "test_suites" / "Access-Management.md"


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setenv("PAIS_MODE", "mock")
    monkeypatch.setenv("PAIS_LOG_LEVEL", "WARNING")
    return CliRunner()


def test_preview_test_suite_bge_returns_expected_chunk_count() -> None:
    report = preview("test_suite_bge", FIXTURE)
    assert report.files_scanned == 1
    assert report.chunks_emitted == 12  # 1 overview + 11 test cases
    assert report.char_stats.median > 0


def test_preview_provides_token_distribution_when_tokenizers_installed() -> None:
    report = preview("test_suite_bge", FIXTURE)
    if report.token_stats is None:
        pytest.skip("tokenizers not installed in this env")
    assert report.token_stats.median > 0
    assert report.char_token_ratio_median is not None
    # English text under bge-small ≈ 3-5 chars/token.
    assert 2.0 <= report.char_token_ratio_median <= 6.0


def test_preview_exposes_suggested_index_config() -> None:
    report = preview("test_suite_bge", FIXTURE)
    assert report.target_embeddings_model == "BAAI/bge-small-en-v1.5"
    assert report.suggested_index_chunk_size == 512
    assert report.suggested_index_chunk_overlap == 64


def test_preview_includes_sample_chunk() -> None:
    report = preview("test_suite_bge", FIXTURE)
    assert report.sample_chunk_first300
    assert "Suite: Access-Management" in report.sample_chunk_first300


def test_preview_directory_walk(tmp_path: Path) -> None:
    for i in range(3):
        (tmp_path / f"suite-{i}.md").write_text(
            FIXTURE.read_text().replace("Access-Management", f"Suite{i}")
        )
    report = preview("test_suite_bge", tmp_path)
    assert report.files_scanned == 3
    assert report.chunks_emitted >= 3


def test_preview_respects_limit(tmp_path: Path) -> None:
    for i in range(5):
        (tmp_path / f"suite-{i}.md").write_text(
            FIXTURE.read_text().replace("Access-Management", f"Suite{i}")
        )
    report = preview("test_suite_bge", tmp_path, limit=2)
    assert report.files_scanned == 2
    assert report.truncated is True


def test_preview_dump_writes_every_chunk_to_disk(tmp_path: Path) -> None:
    out = tmp_path / "dump"
    report = preview("test_suite_bge", FIXTURE, dump_to=out)
    assert out.is_dir()
    assert len(list(out.iterdir())) == report.chunks_emitted
    assert report.dump_dir == str(out)
    assert len(report.dumped) == report.chunks_emitted
    for d in report.dumped:
        assert Path(d.path).exists()
        assert Path(d.path).stat().st_size == d.bytes


def test_preview_show_all_populates_first_chars_without_disk_write() -> None:
    report = preview("test_suite_bge", FIXTURE, show_all=True)
    assert report.show_all is True
    assert report.dump_dir is None
    assert len(report.dumped) == report.chunks_emitted
    assert all(d.first_chars for d in report.dumped)
    assert all(d.path == "" for d in report.dumped)  # no disk write


def test_preview_command_renders_table(runner: CliRunner) -> None:
    r = runner.invoke(cli_app, ["splitters", "preview", "test_suite_bge", str(FIXTURE)])
    assert r.exit_code == 0, r.output
    assert "Chunks emitted" in r.output
    assert "Recommended index config" in r.output
    assert "BAAI/bge-small-en-v1.5" in r.output


def test_preview_command_dump_flag(runner: CliRunner, tmp_path: Path) -> None:
    out = tmp_path / "dump"
    r = runner.invoke(
        cli_app,
        ["splitters", "preview", "test_suite_bge", str(FIXTURE), "--dump", str(out)],
    )
    assert r.exit_code == 0, r.output
    assert "Wrote" in r.output
    assert out.exists()
    assert len(list(out.iterdir())) == 12


def test_preview_command_json_output_is_parseable(runner: CliRunner) -> None:
    r = runner.invoke(
        cli_app, ["splitters", "preview", "test_suite_bge", str(FIXTURE), "-o", "json"]
    )
    assert r.exit_code == 0, r.output
    data = json.loads(r.output)
    assert data["kind"] == "test_suite_bge"
    assert data["chunks_emitted"] > 0
    assert data["target_embeddings_model"] == "BAAI/bge-small-en-v1.5"
    assert data["suggested_index_chunk_size"] == 512
