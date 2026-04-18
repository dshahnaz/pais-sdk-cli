"""End-to-end tests for `pais repro` against the in-process mock store."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pais.cli.app import app as cli_app


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> CliRunner:
    """Mock-mode CLI with isolated ~/.pais/logs."""
    log_dir = tmp_path / ".pais" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "pais.log").write_text("", encoding="utf-8")

    from pais.cli import repro_cmd, support_bundle_cmd

    monkeypatch.setenv("PAIS_MODE", "mock")
    monkeypatch.setenv("PAIS_LOG_LEVEL", "WARNING")
    monkeypatch.setenv("PAIS_LOG_FILE", str(log_dir / "pais.log"))
    monkeypatch.setattr(repro_cmd, "_LOG_DIR", log_dir)
    monkeypatch.setattr(support_bundle_cmd, "_LOG_DIR", log_dir)
    return CliRunner()


@pytest.fixture
def fixtures(tmp_path: Path) -> dict[str, Path]:
    """Build a self-contained suites dir + instructions + 2 prompt files under tmp."""
    repo_fixture = Path(__file__).parent / "fixtures" / "test_suites" / "Access-Management.md"
    suites_dir = tmp_path / "suites"
    suites_dir.mkdir()
    (suites_dir / "Access-Management.md").write_text(
        repo_fixture.read_text(encoding="utf-8"), encoding="utf-8"
    )

    instructions = tmp_path / "instructions.md"
    instructions.write_text("You are a test-suite reviewer. Be terse.\n", encoding="utf-8")

    prompt1 = tmp_path / "prompt_a.md"
    prompt1.write_text("List the suites.\n", encoding="utf-8")
    prompt2 = tmp_path / "prompt_b.md"
    prompt2.write_text("Summarize the access management suite.\n", encoding="utf-8")

    return {
        "suites_dir": suites_dir,
        "instructions": instructions,
        "prompt1": prompt1,
        "prompt2": prompt2,
    }


def test_repro_e2e_mock(runner: CliRunner, fixtures: dict[str, Path], tmp_path: Path) -> None:
    """End-to-end: create KB+index+agent, ingest, run 2 prompts, bundle."""
    out = tmp_path / "repro.zip"
    r = runner.invoke(
        cli_app,
        [
            "repro",
            "--suites-dir",
            str(fixtures["suites_dir"]),
            "--instructions",
            str(fixtures["instructions"]),
            "--prompts",
            str(fixtures["prompt1"]),
            "--prompts",
            str(fixtures["prompt2"]),
            "--output",
            str(out),
        ],
    )
    assert r.exit_code == 0, r.output

    assert out.exists()
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert "manifest.json" in names
        assert "responses/prompt_a.md.json" in names
        assert "responses/prompt_b.md.json" in names

        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        assert manifest["recipe"]["splitter"] == "test_suite_bge"
        assert manifest["recipe"]["chunk_size"] == 400
        assert manifest["recipe"]["index_top_n"] == 5
        assert manifest["kb_id"]
        assert manifest["index_id"]
        assert manifest["agent_id"]
        # Sensitive content must NOT be in manifest by default.
        assert "instructions" not in manifest["recipe"] or isinstance(
            manifest["recipe"].get("instructions"), str
        )
        # Instructions text not bundled by default.
        assert "instructions.md" not in names

        rec1 = json.loads(z.read("responses/prompt_a.md.json").decode("utf-8"))
        assert rec1["prompt_file"] == "prompt_a.md"
        assert rec1["ok"] is True
        assert rec1["prompt_bytes"] == len(b"List the suites.\n")
        assert rec1["finish_reason"] is not None


def test_repro_include_instructions_opt_in(
    runner: CliRunner, fixtures: dict[str, Path], tmp_path: Path
) -> None:
    """--include-instructions writes the md file into the bundle root."""
    out = tmp_path / "repro_inc.zip"
    r = runner.invoke(
        cli_app,
        [
            "repro",
            "--suites-dir",
            str(fixtures["suites_dir"]),
            "--instructions",
            str(fixtures["instructions"]),
            "--prompts",
            str(fixtures["prompt1"]),
            "--include-instructions",
            "--output",
            str(out),
        ],
    )
    assert r.exit_code == 0, r.output
    with zipfile.ZipFile(out) as z:
        assert "instructions.md" in z.namelist()
        assert "test-suite reviewer" in z.read("instructions.md").decode("utf-8")


def test_repro_cleanup_deletes_resources(
    runner: CliRunner, fixtures: dict[str, Path], tmp_path: Path
) -> None:
    """--cleanup removes the agent + KB created during the run."""
    out = tmp_path / "repro_clean.zip"
    r = runner.invoke(
        cli_app,
        [
            "repro",
            "--suites-dir",
            str(fixtures["suites_dir"]),
            "--instructions",
            str(fixtures["instructions"]),
            "--prompts",
            str(fixtures["prompt1"]),
            "--cleanup",
            "--output",
            str(out),
        ],
    )
    assert r.exit_code == 0, r.output
    with zipfile.ZipFile(out) as z:
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        # Cleanup ran without error.
        assert "cleanup_agent_error" not in manifest
        assert "cleanup_kb_error" not in manifest
        assert manifest["recipe"]["cleanup"] is True
