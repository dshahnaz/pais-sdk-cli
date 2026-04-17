"""Per-resource CRUD + happy-path tests, parametrized over fake and live-HTTP mocks."""

from __future__ import annotations

import pytest

from pais.client import PaisClient
from pais.errors import PaisNotFoundError, PaisValidationError
from pais.models import (
    AgentCreate,
    ChatCompletionRequest,
    ChatMessage,
    EmbeddingRequest,
    IndexCreate,
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
    SearchQuery,
    ToolLink,
    ToolLinkType,
)


def test_kb_crud(any_client: PaisClient) -> None:
    listing = any_client.knowledge_bases.list()
    assert listing.object == "list"
    assert listing.data == []

    kb = any_client.knowledge_bases.create(KnowledgeBaseCreate(name="docs", description="manuals"))
    assert kb.name == "docs"
    assert kb.data_origin_type == "LOCAL_FILES"

    got = any_client.knowledge_bases.get(kb.id)
    assert got.id == kb.id

    updated = any_client.knowledge_bases.update(kb.id, KnowledgeBaseUpdate(description="updated"))
    assert updated.description == "updated"

    any_client.knowledge_bases.delete(kb.id)
    with pytest.raises(PaisNotFoundError):
        any_client.knowledge_bases.get(kb.id)


def test_kb_create_missing_name_raises_validation(any_client: PaisClient) -> None:
    with pytest.raises(PaisValidationError):
        any_client.knowledge_bases._create({})  # raw dict, no name


def test_index_upload_search_flow(any_client: PaisClient) -> None:
    kb = any_client.knowledge_bases.create(KnowledgeBaseCreate(name="code"))
    ix = any_client.indexes.create(
        kb.id,
        IndexCreate(
            name="code-index",
            embeddings_model_endpoint="BAAI/bge-small-en-v1.5",
            chunk_size=80,
            chunk_overlap=20,
        ),
    )
    assert ix.kb_id == kb.id

    # Upload a document via multipart.
    import os
    import tempfile

    text = (
        "The WorkflowManager coordinates task execution. "
        "Agents execute steps defined in Plans. "
        "Validation errors surface early to callers."
    )
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        fh.write(text)
        fh.flush()
        path = fh.name
    try:
        doc = any_client.indexes.upload_document(kb.id, ix.id, path)
    finally:
        os.unlink(path)
    assert doc.state == "INDEXED"
    assert doc.chunk_count and doc.chunk_count > 0

    # Wait-for-indexing returns the DONE record.
    indexing = any_client.indexes.wait_for_indexing(
        kb.id, ix.id, timeout=2.0, interval=0.01, sleep=lambda _: None
    )
    assert indexing.state == "DONE"

    # Search surfaces a hit from the uploaded text.
    result = any_client.indexes.search(kb.id, ix.id, SearchQuery(query="WorkflowManager", top_n=3))
    assert result.hits, "expected at least one search hit"
    assert any("WorkflowManager" in hit.text for hit in result.hits)


def test_mcp_tools_and_agent_chat(any_client: PaisClient) -> None:
    kb = any_client.knowledge_bases.create(KnowledgeBaseCreate(name="code2"))
    ix = any_client.indexes.create(
        kb.id,
        IndexCreate(name="ix", embeddings_model_endpoint="BAAI/bge-small-en-v1.5"),
    )

    import os
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        fh.write("Knowledge base answer: the answer is 42. Always has been.")
        fh.flush()
        doc_path = fh.name
    try:
        any_client.indexes.upload_document(kb.id, ix.id, doc_path)
    finally:
        os.unlink(doc_path)

    # MCP tool listing includes a KB-search tool for the new index.
    tool = any_client.mcp_tools.find_kb_search_tool()
    assert tool is not None
    assert tool.id.endswith(ix.id) or "kbsearch" in tool.id

    agent = any_client.agents.create(
        AgentCreate(
            name="qa-agent",
            model="openai/gpt-oss-120b-4x",
            instructions="Use the KB to answer.",
            tools=[
                ToolLink(
                    link_type=ToolLinkType.PAIS_KNOWLEDGE_BASE_INDEX_SEARCH_TOOL_LINK,
                    tool_id=tool.id,
                    top_n=3,
                    similarity_cutoff=0.0,
                )
            ],
        )
    )
    assert agent.tools[0].tool_id == tool.id

    resp = any_client.agents.chat(
        agent.id,
        ChatCompletionRequest(messages=[ChatMessage(role="user", content="What is the answer?")]),
    )
    assert resp.choices[0].message.content
    assert "the answer is 42" in (resp.choices[0].message.content or "").lower() or any(
        "42" in (h.get("text", "") if isinstance(h, dict) else "") for h in (resp.references or [])
    )


def test_models_and_embeddings(any_client: PaisClient) -> None:
    models = any_client.models.list()
    assert any(m.model_type and m.model_type == "COMPLETIONS" for m in models.data)

    emb = any_client.embeddings.create(
        EmbeddingRequest(model="BAAI/bge-small-en-v1.5", input=["hello", "world"])
    )
    assert len(emb.data) == 2
    assert len(emb.data[0].embedding) > 0


def test_agent_chat_streaming(any_client: PaisClient) -> None:
    agent = any_client.agents.create(
        AgentCreate(name="stream-agent", model="openai/gpt-oss-120b-4x")
    )
    chunks = b"".join(
        any_client.agents.chat_stream(
            agent.id,
            ChatCompletionRequest(messages=[ChatMessage(role="user", content="Hi")]),
        )
    )
    assert b"data:" in chunks
    assert b"[DONE]" in chunks
