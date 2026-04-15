"""H8 gate: a minimal external script can use `PaisClient(transport=...)`
without the CLI or Settings. Proves the embedding seam is clean."""

from __future__ import annotations

from pais import PaisClient
from pais.models import (
    AgentCreate,
    ChatCompletionRequest,
    ChatMessage,
    IndexCreate,
    KnowledgeBaseCreate,
    ToolLink,
    ToolLinkType,
)
from pais.transport.fake_transport import FakeTransport
from pais_mock.state import Store


def test_external_embed_seam_kb_to_chat(tmp_path) -> None:
    # Exactly how a host app would wire in: bring your own Store (or real transport).
    client = PaisClient(transport=FakeTransport(Store()))

    kb = client.knowledge_bases.create(KnowledgeBaseCreate(name="embed-demo"))
    ix = client.indexes.create(
        kb.id,
        IndexCreate(name="ix", embeddings_model_endpoint="BAAI/bge-small-en-v1.5"),
    )
    doc_file = tmp_path / "notes.txt"
    doc_file.write_text("The capital of France is Paris.")
    client.indexes.upload_document(kb.id, ix.id, doc_file)

    tool = client.mcp_tools.find_kb_search_tool()
    assert tool is not None

    agent = client.agents.create(
        AgentCreate(
            name="a",
            model="openai/gpt-oss-120b-4x",
            tools=[
                ToolLink(
                    link_type=ToolLinkType.PAIS_KNOWLEDGE_BASE_INDEX_SEARCH_TOOL_LINK,
                    tool_id=tool.id,
                )
            ],
        )
    )
    resp = client.agents.chat(
        agent.id,
        ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="What is the capital of France?")]
        ),
    )
    assert resp.choices[0].message.content
    client.close()
