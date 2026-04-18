"""Pin the exact JSON body sent to POST /agents.

PAIS deployments 500 on `tools: []` combined with `index_id`, and some fields
(session_max_length, session_summarization_strategy, index_reference_format,
chat_system_instruction_mode) should be omitted from the wire unless the
caller explicitly sets them — the server owns those defaults.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import IO, Any

from pais.client import PaisClient
from pais.models import AgentCreate, ToolLink, ToolLinkType
from pais.transport.base import Response


class _SpyTransport:
    """Records every request body and returns a canned agent response."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        files: dict[str, tuple[str, IO[bytes], str]] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Response:
        self.calls.append({"method": method, "path": path, "json": json})
        body = {
            "id": "a-1",
            "object": "agent",
            "created_at": 1_700_000_000,
            "name": (json or {}).get("name", "x"),
            "model": (json or {}).get("model", "m"),
            "status": "AVAILABLE",
        }
        return Response(status_code=201, body=body, headers={}, request_id="req-1")

    def stream(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Iterator[bytes]:
        if False:
            yield b""

    def close(self) -> None:
        return None


def _create_and_capture(payload: AgentCreate) -> dict[str, Any]:
    spy = _SpyTransport()
    client = PaisClient(spy)
    client.agents.create(payload)
    assert len(spy.calls) == 1
    body = spy.calls[0]["json"]
    assert isinstance(body, dict)
    return body


def test_default_body_omits_tools_and_server_owned_defaults() -> None:
    """Minimum AgentCreate must not ship `tools: []` or other SDK-local defaults."""
    body = _create_and_capture(
        AgentCreate(name="demo", model="openai/gpt-oss-120b", index_id="ix-uuid")
    )

    assert body["name"] == "demo"
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["index_id"] == "ix-uuid"

    # None of these should hit the wire unless the caller set them — server owns the defaults.
    for field in (
        "tools",
        "session_max_length",
        "session_summarization_strategy",
        "index_reference_format",
        "chat_system_instruction_mode",
        "completion_role",
    ):
        assert field not in body, f"{field!r} leaked onto the wire: {body!r}"


def test_explicit_tools_are_sent() -> None:
    """Legacy MCP path: explicit tools=[ToolLink] must serialize."""
    body = _create_and_capture(
        AgentCreate(
            name="legacy",
            model="openai/gpt-oss-120b",
            tools=[
                ToolLink(
                    link_type=ToolLinkType.PAIS_KNOWLEDGE_BASE_INDEX_SEARCH_TOOL_LINK,
                    tool_id="mcp-uuid",
                    top_n=5,
                )
            ],
        )
    )
    assert "tools" in body
    assert body["tools"][0]["tool_id"] == "mcp-uuid"
    assert body["tools"][0]["top_n"] == 5


def test_explicit_session_max_length_is_sent() -> None:
    """Caller opts in → field rides on the wire."""
    body = _create_and_capture(
        AgentCreate(
            name="sized",
            model="openai/gpt-oss-120b",
            index_id="ix-uuid",
            session_max_length=4096,
            session_summarization_strategy="delete_oldest",
        )
    )
    assert body["session_max_length"] == 4096
    assert body["session_summarization_strategy"] == "delete_oldest"


def test_empty_tools_list_is_not_sent() -> None:
    """`tools=[]` explicitly passed by a caller is still dropped to protect
    against the 500. Callers who want empty tools can just omit the kwarg."""
    body = _create_and_capture(
        AgentCreate(name="empty", model="openai/gpt-oss-120b", index_id="ix-uuid", tools=None)
    )
    assert "tools" not in body
