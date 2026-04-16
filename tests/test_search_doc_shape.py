"""Search wire format matches the Broadcom doc.

Doc shapes verified at <https://developer.broadcom.com/xapis/vmware-private-ai-service-api/latest/>:
  request:  {"text": "...", "top_k": N, "similarity_cutoff": F}
  response: {"chunks": [{origin_name, origin_ref, document_id, score, media_type, text}]}

Our SDK keeps `query` / `top_n` as Python field names for back-compat but
serializes/parses to the doc-aligned wire format."""

from __future__ import annotations

from pais.models.index import SearchHit, SearchQuery, SearchResponse


def test_search_query_dumps_doc_aligned_keys() -> None:
    """`by_alias=True` produces the doc-aligned wire body."""
    q = SearchQuery(query="hello", top_n=10, similarity_cutoff=0.2)
    wire = q.model_dump(mode="json", by_alias=True)
    assert wire == {"text": "hello", "top_k": 10, "similarity_cutoff": 0.2}


def test_search_query_python_api_unchanged() -> None:
    """Constructor still accepts `query=` and `top_n=` (back-compat)."""
    q = SearchQuery(query="x", top_n=3)
    assert q.query == "x"
    assert q.top_n == 3


def test_search_query_python_constructor_accepts_field_name_only() -> None:
    """Constructor uses Python field names — query/top_n. Wire output uses aliases."""
    q = SearchQuery(query="x", top_n=7)
    assert q.model_dump(by_alias=True) == {"text": "x", "top_k": 7, "similarity_cutoff": 0.0}


def test_search_response_parses_doc_chunks_shape() -> None:
    """The doc-aligned `{chunks: [...]}` response → `.hits` populated."""
    raw = {
        "chunks": [
            {
                "document_id": "d1",
                "origin_name": "f.md",
                "origin_ref": "p/f.md",
                "score": 0.9,
                "media_type": "text/markdown",
                "text": "hello world",
            }
        ]
    }
    r = SearchResponse.model_validate(raw)
    assert len(r.hits) == 1
    h = r.hits[0]
    assert h.text == "hello world"
    assert h.media_type == "text/markdown"
    assert h.origin_ref == "p/f.md"
    assert h.chunk_id is None  # absent in doc shape


def test_search_response_parses_legacy_hits_shape() -> None:
    """Older PAIS deployments returning `{hits: [...]}` still parse."""
    raw = {
        "hits": [
            {
                "document_id": "d1",
                "chunk_id": "c1",
                "text": "t",
                "score": 0.5,
                "origin_name": "f.md",
            }
        ]
    }
    r = SearchResponse.model_validate(raw)
    assert len(r.hits) == 1
    assert r.hits[0].chunk_id == "c1"


def test_search_hit_tolerates_extra_fields() -> None:
    """`extra='allow'` (inherited from PaisModel) lets new server fields slide through."""
    h = SearchHit.model_validate(
        {
            "document_id": "d1",
            "text": "t",
            "score": 0.1,
            "future_field_added_in_v2": "ignored gracefully",
        }
    )
    assert h.text == "t"


def test_end_to_end_search_via_mock_returns_hits() -> None:
    """Round-trip through the SDK + mock: doc-aligned both directions."""
    import tempfile
    from pathlib import Path

    from pais.client import PaisClient
    from pais.models import IndexCreate, KnowledgeBaseCreate
    from pais.transport.fake_transport import FakeTransport
    from pais_mock.state import Store

    c = PaisClient(FakeTransport(Store()))
    kb = c.knowledge_bases.create(KnowledgeBaseCreate(name="k"))
    ix = c.indexes.create(
        kb.id, IndexCreate(name="ix", embeddings_model_endpoint="BAAI/bge-small-en-v1.5")
    )
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False, mode="w") as f:
        f.write("hello world this is a test")
        p = Path(f.name)
    c.indexes.upload_document(kb.id, ix.id, p)
    res = c.indexes.search(kb.id, ix.id, SearchQuery(query="hello"))
    assert len(res.hits) == 1
    assert "hello" in res.hits[0].text
    assert res.hits[0].media_type == "text/markdown"
    assert res.hits[0].origin_ref is not None
