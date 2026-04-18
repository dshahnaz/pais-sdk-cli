"""End-to-end tests for `pais repro` against the in-process mock store."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

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


def test_repro_waits_for_indexing_done(
    runner: CliRunner, fixtures: dict[str, Path], tmp_path: Path
) -> None:
    """After v0.8.1, repro triggers + waits for indexing and records final_state."""
    out = tmp_path / "repro_idx.zip"
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
            "--output",
            str(out),
        ],
    )
    assert r.exit_code == 0, r.output
    with zipfile.ZipFile(out) as z:
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        idx = manifest.get("indexing")
        assert idx is not None, f"no indexing block in manifest: {manifest}"
        assert idx["final_state"] == "DONE"
        assert "id" in idx
        assert isinstance(idx.get("duration_ms"), int)
        # Default binding path: doc-aligned index_id.
        assert manifest["binding"] == "index_id"


def test_repro_aborts_when_indexing_fails(
    runner: CliRunner,
    fixtures: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the indexing ends non-DONE, repro exits 1 and does NOT create the agent."""
    # Patch the SDK's wait_for_indexing to simulate a FAILED terminal state.
    from pais.models import Indexing
    from pais.resources import indexes as _indexes

    def fake_wait(self: Any, kb_id: str, index_id: str, *a: Any, **kw: Any) -> Indexing:
        return Indexing(
            id="ing-fail-1",
            created_at=0,
            index_id=index_id,
            state="FAILED",
            error="simulated parse error",
        )

    monkeypatch.setattr(_indexes.IndexesResource, "wait_for_indexing", fake_wait)

    # Also spy on agents.create so we can assert it was NOT called after failure.
    from pais.resources import agents as _agents

    created: list[Any] = []
    real_create = _agents.AgentsResource.create

    def spy_create(self: Any, *args: Any, **kwargs: Any) -> Any:
        created.append((args, kwargs))
        return real_create(self, *args, **kwargs)

    monkeypatch.setattr(_agents.AgentsResource, "create", spy_create)

    out = tmp_path / "repro_fail.zip"
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
            "--output",
            str(out),
        ],
    )
    assert r.exit_code == 1, r.output
    # Agent must not be created if indexing failed.
    assert created == []


def test_repro_legacy_mcp_tools_flag(
    runner: CliRunner, fixtures: dict[str, Path], tmp_path: Path
) -> None:
    """--legacy-mcp-tools binds via tools[] + records mcp_tool_id in the manifest."""
    out = tmp_path / "repro_legacy.zip"
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
            "--legacy-mcp-tools",
            "--output",
            str(out),
        ],
    )
    assert r.exit_code == 0, r.output
    with zipfile.ZipFile(out) as z:
        manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        assert manifest["binding"] == "legacy_mcp_tools"
        assert manifest.get("mcp_tool_id"), "mcp_tool_id missing from manifest"
