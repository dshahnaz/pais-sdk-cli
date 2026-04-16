"""Contract test: doc-aligned `index_id` + `index_top_n` shape on AgentCreate
round-trips through the SDK and the mock server.

The published Broadcom PAIS spec
(https://developer.broadcom.com/xapis/vmware-private-ai-service-api/latest/)
specifies that `POST /compatibility/openai/v1/agents` takes `index_id` and
`index_top_n` directly. We add this shape alongside the legacy `tools=[ToolLink]`
to keep both deployments working."""

from __future__ import annotations

from pais.client import PaisClient
from pais.models import (
    AgentCreate,
    DataOriginType,
    IndexCreate,
    KnowledgeBaseCreate,
)
from pais.transport.fake_transport import FakeTransport
from pais_mock.state import Store


def test_agent_create_with_index_id_round_trips() -> None:
    """Doc-aligned shape: agent points at an index UUID via index_id."""
    client = PaisClient(FakeTransport(Store()))
    kb = client.knowledge_bases.create(KnowledgeBaseCreate(name="kb1"))
    ix = client.indexes.create(
        kb.id,
        IndexCreate(name="ix1", embeddings_model_endpoint="BAAI/bge-small-en-v1.5"),
    )
    agent = client.agents.create(
        AgentCreate(
            name="demo",
            model="openai/gpt-oss-120b-4x",
            instructions="answer from the docs",
            index_id=ix.id,
            index_top_n=5,
        )
    )
    assert agent.id
    # Round-trip preserves the doc-aligned fields.
    assert agent.index_id == ix.id
    assert agent.index_top_n == 5
    # Legacy tools field is empty when index_id is used.
    assert agent.tools == []


def test_agent_create_with_legacy_tools_still_works() -> None:
    """Back-compat: tools=[ToolLink] path still round-trips."""
    from pais.models import ToolLink, ToolLinkType

    client = PaisClient(FakeTransport(Store()))
    agent = client.agents.create(
        AgentCreate(
            name="legacy",
            model="openai/gpt-oss-120b-4x",
            tools=[
                ToolLink(
                    link_type=ToolLinkType.PAIS_KNOWLEDGE_BASE_INDEX_SEARCH_TOOL_LINK,
                    tool_id="some-mcp-uuid",
                    top_n=5,
                )
            ],
        )
    )
    assert len(agent.tools) == 1
    assert agent.tools[0].tool_id == "some-mcp-uuid"
    assert agent.index_id is None  # not set when using legacy path


def test_data_origin_type_accepts_doc_value_data_sources() -> None:
    """The Broadcom doc uses 'DATA_SOURCES' (plural). Our enum supports it."""
    assert DataOriginType.DATA_SOURCES.value == "DATA_SOURCES"
    # Build a KB with the doc-aligned value — must round-trip.
    client = PaisClient(FakeTransport(Store()))
    kb = client.knowledge_bases.create(
        KnowledgeBaseCreate(name="kb_doc", data_origin_type=DataOriginType.DATA_SOURCES)
    )
    assert kb.data_origin_type == DataOriginType.DATA_SOURCES
