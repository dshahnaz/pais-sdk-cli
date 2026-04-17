"""Generic ingest runner: --replace by group_key, --dry-run, error tolerance.

Uses a local fake splitter (not registered) to decouple runner tests from the
behavior of the built-in test-suite splitters.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from pais.client import PaisClient
from pais.ingest.runner import ingest_path
from pais.ingest.splitters._base import SplitDoc
from pais.models import IndexCreate, KnowledgeBaseCreate
from pais.transport.fake_transport import FakeTransport
from pais_mock.state import Store


class _FakeSplitter:
    """Minimal Splitter — 1 file → 1 SplitDoc with body == file bytes."""

    kind = "_fake_passthrough"

    def group_key(self, path: Path) -> str:
        return path.name

    def split(self, path: Path) -> Iterator[SplitDoc]:
        yield SplitDoc(
            origin_name=path.name,
            body=path.read_bytes(),
            media_type="text/plain",
        )


def _provision() -> tuple[PaisClient, str, str]:
    c = PaisClient(FakeTransport(Store()))
    kb = c.knowledge_bases.create(KnowledgeBaseCreate(name="kb"))
    ix = c.indexes.create(kb.id, IndexCreate(name="ix", embeddings_model_endpoint="bge"))
    return c, kb.id, ix.id


def test_uploads_every_file(tmp_path: Path) -> None:
    c, kb, ix = _provision()
    for n in ("a.txt", "b.txt", "c.txt"):
        (tmp_path / n).write_text("x")
    report = ingest_path(c, tmp_path, splitter=_FakeSplitter(), kb_id=kb, index_id=ix, workers=2)
    assert report.total_files == 3
    assert report.total_chunks_uploaded == 3
    assert report.total_failed == 0
    assert {d.origin_name for d in c.indexes.list_documents(kb, ix).data} == {
        "a.txt",
        "b.txt",
        "c.txt",
    }


def test_replace_only_touches_matching_files(tmp_path: Path) -> None:
    c, kb, ix = _provision()
    for n in ("alpha.txt", "alphabet.txt", "beta.txt"):
        (tmp_path / n).write_text("v1")
    ingest_path(c, tmp_path, splitter=_FakeSplitter(), kb_id=kb, index_id=ix)

    only = tmp_path / "only"
    only.mkdir()
    (only / "alpha.txt").write_text("v2")
    report = ingest_path(c, only, splitter=_FakeSplitter(), kb_id=kb, index_id=ix, replace=True)
    assert report.total_existing_deleted == 1
    docs = {d.origin_name for d in c.indexes.list_documents(kb, ix).data}
    assert docs == {"alpha.txt", "alphabet.txt", "beta.txt"}


def test_dry_run_uploads_nothing(tmp_path: Path) -> None:
    c, kb, ix = _provision()
    (tmp_path / "x.txt").write_text("hello")
    report = ingest_path(c, tmp_path, splitter=_FakeSplitter(), kb_id=kb, index_id=ix, dry_run=True)
    assert report.total_files == 1
    assert report.total_chunks_uploaded == 0
    assert c.indexes.list_documents(kb, ix).data == []


def test_chunk_size_distribution_in_report(tmp_path: Path) -> None:
    c, kb, ix = _provision()
    for i, content in enumerate([b"x", b"yy", b"zzz"]):
        (tmp_path / f"f{i}.txt").write_bytes(content)
    report = ingest_path(c, tmp_path, splitter=_FakeSplitter(), kb_id=kb, index_id=ix)
    dist = report.chunk_size_distribution
    assert dist["min"] == 1
    assert dist["max"] == 3
    assert dist["count"] == 3
