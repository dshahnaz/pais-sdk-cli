"""`/control/mcp-servers/tools` is undocumented (CLAUDE.md constraint #8).

Shape drift in the wild must not crash callers: `McpToolsResource.list`
returns an empty `ListResponse` on `ValidationError`, logs one warning, and
every downstream flow degrades gracefully to manual entry (v0.7.1).
"""

from __future__ import annotations

import pytest

from pais.models.common import ListResponse
from pais.models.mcp import McpTool
from pais.resources.mcp_tools import McpToolsResource


class _StubTransport:
    """Minimal transport that returns a canned payload for any request."""

    def __init__(self, body: object) -> None:
        self._body = body

    def request(self, method: str, path: str, **_: object) -> object:
        class _R:
            body = self._body

        return _R()


def _resource(body: object) -> McpToolsResource:
    return McpToolsResource(_StubTransport(body))  # type: ignore[arg-type]


def test_list_returns_empty_on_malformed_data(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Server returns a tool with wrong-typed fields → empty list, no raise."""
    import pais.resources.mcp_tools as mod

    monkeypatch.setattr(mod, "_warned_shape", False, raising=False)
    resource = _resource(
        {
            "object": "list",
            "data": [{"id": "t1", "server": 42, "input_schema": "not-a-dict"}],
            "has_more": False,
        }
    )
    with caplog.at_level("WARNING"):
        result = resource.list()
    assert isinstance(result, ListResponse)
    assert result.data == []
    assert result.has_more is False


def test_list_returns_empty_on_completely_broken_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pais.resources.mcp_tools as mod

    monkeypatch.setattr(mod, "_warned_shape", False, raising=False)
    resource = _resource({"unexpected": "shape"})
    result = resource.list()
    assert result.data == []


def test_list_parses_valid_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Well-formed payload still round-trips."""
    import pais.resources.mcp_tools as mod

    monkeypatch.setattr(mod, "_warned_shape", False, raising=False)
    resource = _resource(
        {
            "object": "list",
            "data": [
                {"id": "t1", "name": "kb-search", "server": "built-in"},
                {"id": "t2"},  # minimal shape is now accepted
            ],
            "has_more": False,
        }
    )
    result = resource.list()
    assert len(result.data) == 2
    assert result.data[0].name == "kb-search"
    assert result.data[1].id == "t2"
    assert all(isinstance(t, McpTool) for t in result.data)


def test_find_kb_search_tool_degrades_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the endpoint is malformed, `find_kb_search_tool` returns None
    instead of raising — callers can then fall through to manual entry."""
    import pais.resources.mcp_tools as mod

    monkeypatch.setattr(mod, "_warned_shape", False, raising=False)
    resource = _resource({"data": [{"id": "t1", "server": {"nested": "bad"}}]})
    assert resource.find_kb_search_tool() is None
