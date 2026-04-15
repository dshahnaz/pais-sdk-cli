"""MCP tools discovery."""

from __future__ import annotations

from typing import Any

from pais.models.common import ListResponse
from pais.models.mcp import McpTool
from pais.resources._base import Resource


class McpToolsResource(Resource[McpTool]):
    path = "/control/mcp-servers/tools"
    model = McpTool

    def list(self, *, server: str = "built-in") -> ListResponse[McpTool]:  # type: ignore[override]
        params: dict[str, Any] = {"server": server}
        raw = self._get_json(self.path, params=params)
        return ListResponse[McpTool].model_validate(raw)

    def find_kb_search_tool(self, *, server: str = "built-in") -> McpTool | None:
        """Return the most-recent KB-index-search MCP tool, or None if absent."""
        tools = self.list(server=server).data
        candidates = [
            t for t in tools if t.description and "knowledge base index" in t.description.lower()
        ] or [
            t
            for t in tools
            if "search" in (t.name or "").lower() and "knowledge" in (t.name or "").lower()
        ]
        return candidates[-1] if candidates else None
