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
    name: str
    description: str | None = None
    server: str = "built-in"
    input_schema: dict[str, Any] | None = None
