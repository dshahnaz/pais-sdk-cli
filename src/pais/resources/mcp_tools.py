"""MCP tools discovery."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from pais.logging import get_logger
from pais.models.common import ListResponse
from pais.models.mcp import McpTool
from pais.resources._base import Resource

_log = get_logger(__name__)
_warned_shape = False


class McpToolsResource(Resource[McpTool]):
    path = "/control/mcp-servers/tools"
    model = McpTool

    def list(self, *, server: str = "built-in") -> ListResponse[McpTool]:  # type: ignore[override]
        # `/control/mcp-servers/tools` is undocumented (CLAUDE.md constraint #8).
        # On shape drift we warn once and return an empty list so the interactive
        # picker degrades to its manual-entry fallback instead of crashing.
        global _warned_shape
        params: dict[str, Any] = {"server": server}
        raw = self._get_json(self.path, params=params)
        try:
            return ListResponse[McpTool].model_validate(raw)
        except ValidationError as exc:
            if not _warned_shape:
                _log.warning(
                    "mcp_tools_unexpected_shape",
                    detail="MCP tools endpoint returned an unexpected shape; "
                    "returning empty list. Endpoint is undocumented — prefer "
                    "linking agents via `index_id` instead of `--kb-search-tool`.",
                    errors=exc.error_count(),
                )
                _warned_shape = True
            return ListResponse[McpTool](object="list", data=[], has_more=False)

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
