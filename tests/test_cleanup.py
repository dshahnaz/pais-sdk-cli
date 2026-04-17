"""Cleanup ops: index purge, KB purge, with all three strategies."""

from __future__ import annotations

import io

import pytest

from pais.client import PaisClient
from pais.models import IndexCreate, KnowledgeBaseCreate
from pais.transport.fake_transport import FakeTransport
from pais_mock.state import Store


def _provision(client: PaisClient, n_indexes: int = 1) -> tuple[str, list[str]]:
    kb = client.knowledge_bases.create(KnowledgeBaseCreate(name="kb"))
    ix_ids = []
    for i in range(n_indexes):
        ix = client.indexes.create(
            kb.id, IndexCreate(name=f"ix{i}", embeddings_model_endpoint="bge")
        )
        ix_ids.append(ix.id)
    return kb.id, ix_ids


def _upload(client: PaisClient, kb_id: str, ix_id: str, names: list[str]) -> None:
    for name in names:
        files = {"file": (name, io.BytesIO(b"body"), "text/markdown")}
        client._transport.request(
            "POST",
            f"/control/knowledge-bases/{kb_id}/indexes/{ix_id}/documents",
            files=files,
        )


def test_purge_api_deletes_all_documents() -> None:
    store = Store()
    c = PaisClient(FakeTransport(store))
    kb_id, [ix_id] = _provision(c)
    _upload(c, kb_id, ix_id, ["a.md", "b.md", "c.md"])

    res = c.indexes.purge(kb_id, ix_id, strategy="api")
    assert res.strategy_used == "api"
    assert res.documents_deleted == 3
    assert res.errors == []
    assert c.indexes.list_documents(kb_id, ix_id).data == []


def test_purge_with_match_origin_prefix_only_targets_matching_docs() -> None:
    store = Store()
    c = PaisClient(FakeTransport(store))
    kb_id, [ix_id] = _provision(c)
    _upload(c, kb_id, ix_id, ["Suite-A__1.md", "Suite-A__2.md", "Suite-B__1.md"])

    res = c.indexes.purge(kb_id, ix_id, strategy="api", match_origin_prefix="Suite-A__")
    assert res.documents_deleted == 2
    remaining = [d.origin_name for d in c.indexes.list_documents(kb_id, ix_id).data]
    assert remaining == ["Suite-B__1.md"]


def test_purge_recreate_strategy_deletes_index_and_makes_new() -> None:
    store = Store()
    c = PaisClient(FakeTransport(store))
    kb_id, [ix_id] = _provision(c)
    _upload(c, kb_id, ix_id, ["a.md", "b.md"])

    res = c.indexes.purge(kb_id, ix_id, strategy="recreate")
    assert res.strategy_used == "recreate"
    assert res.documents_deleted == 2
    assert res.new_index_id is not None
    assert res.new_index_id != ix_id

    # Old index gone, new index has zero docs and same config.
    indexes = {i.id for i in c.indexes.list(kb_id).data}
    assert ix_id not in indexes
    assert res.new_index_id in indexes
    assert c.indexes.list_documents(kb_id, res.new_index_id).data == []


def test_purge_recreate_refused_with_match_origin_prefix() -> None:
    store = Store()
    c = PaisClient(FakeTransport(store))
    kb_id, [ix_id] = _provision(c)

    with pytest.raises(ValueError, match="cannot be combined"):
        c.indexes.purge(kb_id, ix_id, strategy="recreate", match_origin_prefix="Suite-X__")


def test_purge_auto_falls_back_to_recreate_when_endpoint_disabled() -> None:
    store = Store()
    store.disabled_endpoints.add(("DELETE", "/documents/{id}"))
    c = PaisClient(FakeTransport(store))
    kb_id, [ix_id] = _provision(c)
    _upload(c, kb_id, ix_id, ["a.md", "b.md"])

    res = c.indexes.purge(kb_id, ix_id, strategy="auto")
    assert res.strategy_used == "recreate"
    assert res.new_index_id is not None
    assert res.documents_deleted == 2


def test_purge_api_strategy_aborts_when_match_prefix_and_endpoint_missing() -> None:
    store = Store()
    store.disabled_endpoints.add(("DELETE", "/documents/{id}"))
    c = PaisClient(FakeTransport(store))
    kb_id, [ix_id] = _provision(c)
    _upload(c, kb_id, ix_id, ["Suite-A__a.md"])

    res = c.indexes.purge(kb_id, ix_id, strategy="auto", match_origin_prefix="Suite-A__")
    # auto should not silently nuke the entire index when prefix-scoped — it errors.
    assert res.documents_deleted == 0
    assert res.errors


def test_kb_purge_iterates_every_index() -> None:
    store = Store()
    c = PaisClient(FakeTransport(store))
    kb_id, ix_ids = _provision(c, n_indexes=3)
    for ix_id in ix_ids:
        _upload(c, kb_id, ix_id, [f"{ix_id}__a.md", f"{ix_id}__b.md"])

    res = c.knowledge_bases.purge(kb_id, strategy="api")
    assert res.indexes_processed == 3
    assert res.documents_deleted == 6
    for ix_id in ix_ids:
        assert c.indexes.list_documents(kb_id, ix_id).data == []


def test_list_documents_paginates_via_cursor() -> None:
    """Regression test for v0.6.6: purge used to stop at 100 docs because
    list_documents made a single un-paginated GET. Verify the cursor contract."""
    store = Store()
    c = PaisClient(FakeTransport(store))
    kb_id, [ix_id] = _provision(c)
    _upload(c, kb_id, ix_id, [f"doc_{i:03d}.md" for i in range(250)])

    # Single page at default limit=100 — only first 100 returned, has_more=True.
    page1 = c.indexes.list_documents(kb_id, ix_id, limit=100)
    assert len(page1.data) == 100
    assert page1.has_more is True
    assert page1.last_id is not None
    assert page1.num_objects == 250

    # iter_documents transparently walks every page.
    all_docs = list(c.indexes.iter_documents(kb_id, ix_id, limit=100))
    assert len(all_docs) == 250
    assert {d.origin_name for d in all_docs} == {f"doc_{i:03d}.md" for i in range(250)}


def test_purge_removes_every_document_across_pages() -> None:
    """Regression test for the v0.6.5 bug: kb prune stopped at 100 docs."""
    store = Store()
    c = PaisClient(FakeTransport(store))
    kb_id, [ix_id] = _provision(c)
    _upload(c, kb_id, ix_id, [f"doc_{i:03d}.md" for i in range(250)])

    res = c.indexes.purge(kb_id, ix_id, strategy="api")
    assert res.strategy_used == "api"
    assert res.documents_deleted == 250
    assert res.errors == []
    remaining = list(c.indexes.iter_documents(kb_id, ix_id))
    assert remaining == []


def test_iter_documents_max_pages_caps_runaway_server() -> None:
    """If a server mis-reports has_more=True forever, iter_documents must raise
    rather than hang."""
    store = Store()
    c = PaisClient(FakeTransport(store))
    kb_id, [ix_id] = _provision(c)
    _upload(c, kb_id, ix_id, ["a.md", "b.md"])

    with pytest.raises(RuntimeError, match="max_pages"):
        # Tiny limit + tiny cap — in a healthy server this would finish in 1 page,
        # so 0 iterations trivially exceeds the cap.
        list(c.indexes.iter_documents(kb_id, ix_id, limit=1, max_pages=0))
