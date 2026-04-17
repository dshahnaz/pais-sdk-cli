"""Enum-typed model fields are now `str` per the doc contract. Unknown values
parse without crashing."""

from __future__ import annotations

from pais.models import Index, KnowledgeBase, Model
from pais.models.agent import Agent, ToolLink
from pais.models.index import Document, Indexing


def test_model_engine_accepts_unknown_value() -> None:
    m = Model.model_validate(
        {"id": "x", "model_type": "COMPLETIONS", "model_engine": "LLAMA_CPP", "owned_by": "p"}
    )
    assert m.model_engine == "LLAMA_CPP"


def test_model_engine_accepts_documented_value() -> None:
    m = Model.model_validate(
        {"id": "x", "model_type": "EMBEDDINGS", "model_engine": "VLLM", "owned_by": "p"}
    )
    assert m.model_engine == "VLLM"


def test_index_status_accepts_unknown_value() -> None:
    ix = Index.model_validate(
        {
            "id": "x",
            "created_at": 1,
            "name": "n",
            "embeddings_model_endpoint": "m",
            "status": "MIGRATING",
        }
    )
    assert ix.status == "MIGRATING"


def test_indexing_state_accepts_unknown_value() -> None:
    ing = Indexing.model_validate({"id": "x", "created_at": 1, "state": "PAUSED"})
    assert ing.state == "PAUSED"


def test_document_state_accepts_unknown_value() -> None:
    doc = Document.model_validate(
        {"id": "x", "created_at": 1, "origin_name": "f", "state": "QUEUED"}
    )
    assert doc.state == "QUEUED"


def test_kb_data_origin_type_accepts_unknown_value() -> None:
    kb = KnowledgeBase.model_validate(
        {"id": "x", "created_at": 1, "name": "n", "data_origin_type": "S3_BUCKET"}
    )
    assert kb.data_origin_type == "S3_BUCKET"


def test_tool_link_link_type_accepts_unknown_value() -> None:
    tl = ToolLink.model_validate({"link_type": "FUTURE_TOOL_TYPE", "tool_id": "t1"})
    assert tl.link_type == "FUTURE_TOOL_TYPE"


def test_agent_with_unknown_status() -> None:
    a = Agent.model_validate(
        {"id": "x", "created_at": 1, "name": "n", "model": "m", "status": "MAINTENANCE"}
    )
    assert a.status == "MAINTENANCE"
