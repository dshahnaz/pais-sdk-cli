"""kb ensure: idempotent, mismatch detection, prune gating."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from pais.cli import _alias
from pais.cli._config_file import load_profile_config
from pais.cli.ensure_cmd import EnsureReport, _ensure_for_profile
from pais.client import PaisClient
from pais.models import IndexCreate, KnowledgeBaseCreate
from pais.transport.fake_transport import FakeTransport
from pais_mock.state import Store


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(_alias, "CACHE_PATH", tmp_path / "aliases.json")


def _cfg(tmp_path: Path) -> tuple:
    cfg_path = tmp_path / "pais.toml"
    cfg_path.write_text(
        textwrap.dedent(
            """
            [profiles.default]
            mode = "mock"

            [profiles.default.knowledge_bases.kb1]
            name = "real-kb1"

              [[profiles.default.knowledge_bases.kb1.indexes]]
              alias = "main"
              name = "real-ix-main"
              embeddings_model_endpoint = "BAAI/bge-small-en-v1.5"
              chunk_size = 512
              chunk_overlap = 64

                [profiles.default.knowledge_bases.kb1.indexes.splitter]
                kind = "test_suite_md"

              [[profiles.default.knowledge_bases.kb1.indexes]]
              alias = "raw"
              name = "real-ix-raw"
              embeddings_model_endpoint = "BAAI/bge-small-en-v1.5"

                [profiles.default.knowledge_bases.kb1.indexes.splitter]
                kind = "passthrough"
            """
        )
    )
    return load_profile_config(path=cfg_path, profile="default")


def test_creates_missing_kb_and_indexes(tmp_path: Path) -> None:
    c = PaisClient(FakeTransport(Store()))
    cfg, _, _ = _cfg(tmp_path)

    report = EnsureReport(profile="default")
    _ensure_for_profile(c, cfg, report=report, dry_run=False, prune=False)
    actions = [(r.kind, r.alias, r.action) for r in report.rows]
    assert ("kb", "kb1", "created") in actions
    assert ("index", "kb1:main", "created") in actions
    assert ("index", "kb1:raw", "created") in actions

    kb = next(k for k in c.knowledge_bases.list().data if k.name == "real-kb1")
    assert {i.name for i in c.indexes.list(kb.id).data} == {"real-ix-main", "real-ix-raw"}


def test_idempotent_second_run_marks_existing(tmp_path: Path) -> None:
    c = PaisClient(FakeTransport(Store()))
    cfg, _, _ = _cfg(tmp_path)
    _ensure_for_profile(c, cfg, report=EnsureReport(), dry_run=False, prune=False)

    report = EnsureReport()
    _ensure_for_profile(c, cfg, report=report, dry_run=False, prune=False)
    actions = [(r.kind, r.alias, r.action) for r in report.rows]
    assert ("kb", "kb1", "existing") in actions
    assert ("index", "kb1:main", "existing") in actions


def test_dry_run_does_no_writes(tmp_path: Path) -> None:
    c = PaisClient(FakeTransport(Store()))
    cfg, _, _ = _cfg(tmp_path)
    report = EnsureReport(dry_run=True)
    _ensure_for_profile(c, cfg, report=report, dry_run=True, prune=False)
    actions = [r.action for r in report.rows]
    assert "would-create" in actions
    # No KBs were actually created.
    assert c.knowledge_bases.list().data == []


def test_mismatch_detection(tmp_path: Path) -> None:
    """If server has the same name but different chunk_size, ensure warns."""
    c = PaisClient(FakeTransport(Store()))
    # Pre-create with a different chunk_size than the TOML declares.
    kb = c.knowledge_bases.create(KnowledgeBaseCreate(name="real-kb1"))
    c.indexes.create(
        kb.id,
        IndexCreate(
            name="real-ix-main",
            embeddings_model_endpoint="BAAI/bge-small-en-v1.5",
            chunk_size=128,  # mismatch — TOML says 512
        ),
    )
    cfg, _, _ = _cfg(tmp_path)
    report = EnsureReport()
    _ensure_for_profile(c, cfg, report=report, dry_run=False, prune=False)
    mismatches = [r for r in report.rows if r.action == "mismatch"]
    assert mismatches
    assert "chunk_size" in mismatches[0].detail


def test_prune_dry_run_lists_extras(tmp_path: Path) -> None:
    c = PaisClient(FakeTransport(Store()))
    # Create something not in the TOML.
    c.knowledge_bases.create(KnowledgeBaseCreate(name="orphan-kb"))
    cfg, _, _ = _cfg(tmp_path)
    report = EnsureReport(pruned=True)
    _ensure_for_profile(c, cfg, report=report, dry_run=True, prune=True)
    would_prune = [r for r in report.rows if r.action == "would-prune"]
    assert any(r.name == "orphan-kb" for r in would_prune)
