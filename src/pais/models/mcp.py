"""MCP tool discovery (`/control/mcp-servers/tools`)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pais.models.common import PaisModel


class McpToolLinkType(str, Enum):
    PAIS_KNOWLEDGE_BASE_INDEX_SEARCH_TOOL_LINK = "PAIS_KNOWLEDGE_BASE_INDEX_SEARCH_TOOL_LINK"
    PAIS_MCP_TOOL_LINK = "PAIS_MCP_TOOL_LINK"


class McpTool(PaisModel):
    id: str
    object: Literal["mcp_tool"] = "mcp_tool"
    # `/control/mcp-servers/tools` is not in the published PAIS spec (see
    # CLAUDE.md constraint #8). Field shapes vary across deployments, so
    # everything except `id` is tolerant — callers should use `or`-fallbacks.
    name: str | None = None
    description: str | None = None
    server: str | None = None
    input_schema: dict[str, Any] | None = None
