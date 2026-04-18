"""Pin the exact JSON body sent to POST /agents/<id>/chat/completions.

The SDK previously hard-coded `max_tokens=500` and `temperature=0.7` on
ChatCompletionRequest defaults; both rode the wire even when the caller
didn't ask for them. v0.8.0 drops those defaults to None so the server
owns them. This test pins the new behavior."""

from __future__ import annotations

from collections.abc import Iterator
from typing import IO, Any

from pais.client import PaisClient
from pais.models import ChatCompletionRequest, ChatMessage
from pais.transport.base import Response


class _SpyTransport:
    """Records every request body and returns a canned chat response."""

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
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 1_700_000_000,
            "model": "openai/gpt-oss-120b",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        return Response(status_code=200, body=body, headers={}, request_id="req-1")

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


def test_default_chat_body_has_no_sdk_owned_defaults() -> None:
    """A bare ChatCompletionRequest sends only `messages` + `stream=False`.
    No max_tokens, temperature, top_p, or model unless caller sets them."""
    spy = _SpyTransport()
    client = PaisClient(spy)
    client.agents.chat(
        "agent-1",
        ChatCompletionRequest(messages=[ChatMessage(role="user", content="hi")]),
    )
    assert len(spy.calls) == 1
    body = spy.calls[0]["json"]
    # Server-owned fields must be absent.
    assert "max_tokens" not in body, f"SDK leaked max_tokens onto wire: {body}"
    assert "temperature" not in body, f"SDK leaked temperature onto wire: {body}"
    assert "top_p" not in body
    assert "model" not in body
    # Stream is locally controlled by the resource layer (always False for non-stream).
    assert body.get("stream") is False
    # Messages always present.
    assert body["messages"] == [{"role": "user", "content": "hi"}]


def test_explicit_values_survive_serialization() -> None:
    """Caller-set max_tokens / temperature / top_p / model all reach the wire."""
    spy = _SpyTransport()
    client = PaisClient(spy)
    client.agents.chat(
        "agent-1",
        ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="hi")],
            max_tokens=2048,
            temperature=0.2,
            top_p=0.9,
            model="openai/gpt-oss-120b",
        ),
    )
    body = spy.calls[0]["json"]
    assert body["max_tokens"] == 2048
    assert body["temperature"] == 0.2
    assert body["top_p"] == 0.9
    assert body["model"] == "openai/gpt-oss-120b"


def test_explicit_none_excludes_field() -> None:
    """Explicit None on max_tokens still excludes the field (exclude_none semantics)."""
    spy = _SpyTransport()
    client = PaisClient(spy)
    client.agents.chat(
        "agent-1",
        ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="hi")],
            max_tokens=None,
            temperature=None,
        ),
    )
    body = spy.calls[0]["json"]
    assert "max_tokens" not in body
    assert "temperature" not in body
