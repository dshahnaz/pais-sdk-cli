"""`/control/mcp-servers/tools` is undocumented and drifts between deployments.

`McpTool` must accept minimal / partial payloads so a single weird entry can't
crash the whole interactive flow (v0.7.1).
"""

from __future__ import annotations

from pais.models.mcp import McpTool


def test_mcp_tool_minimal_payload() -> None:
    """Only `id` is required — everything else is tolerant."""
    tool = McpTool.model_validate({"id": "t1"})
    assert tool.id == "t1"
    assert tool.name is None
    assert tool.server is None
    assert tool.description is None
    assert tool.input_schema is None


def test_mcp_tool_accepts_full_payload() -> None:
    tool = McpTool.model_validate(
        {
            "id": "t1",
            "object": "mcp_tool",
            "name": "kb-search",
            "description": "Search the knowledge base index",
            "server": "built-in",
            "input_schema": {"type": "object"},
        }
    )
    assert tool.name == "kb-search"
    assert tool.server == "built-in"
    assert tool.input_schema == {"type": "object"}


def test_mcp_tool_accepts_missing_input_schema() -> None:
    """The bundled mock emits tools without `input_schema` — must validate."""
    tool = McpTool.model_validate(
        {
            "id": "t1",
            "object": "mcp_tool",
            "name": "kb-search",
            "server": "built-in",
        }
    )
    assert tool.input_schema is None
