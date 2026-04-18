"""Workflow E (chat): empty input exits the loop; response rendered."""

from __future__ import annotations

from typing import Any

import pytest
from rich.console import Console

from pais.cli import _recent
from pais.cli._workflows import chat as chat_wf
from pais.client import PaisClient
from pais.config import Settings
from pais.models import AgentCreate
from pais.transport.fake_transport import FakeTransport
from pais_mock.state import Store


class _FakeAsk:
    def __init__(self, value: Any) -> None:
        self._v = value

    def ask(self) -> Any:
        return self._v


class _FakeQ:
    def __init__(self, scripted: list[Any]) -> None:
        self.scripted = list(scripted)

    def text(self, *_a: Any, **_k: Any) -> _FakeAsk:
        return _FakeAsk(self.scripted.pop(0))

    def select(self, *_a: Any, **_k: Any) -> _FakeAsk:
        return _FakeAsk(self.scripted.pop(0))

    def confirm(self, *_a: Any, **_k: Any) -> _FakeAsk:
        return _FakeAsk(self.scripted.pop(0))


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(_recent, "CACHE_PATH", tmp_path / "recent.json")


def test_chat_empty_input_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bare ⏎ on the chat prompt exits the loop without calling the LLM."""
    store = Store()
    client = PaisClient(FakeTransport(store))
    agent = client.agents.create(
        AgentCreate(name="a1", model="openai/gpt-oss-120b-4x", index_id="idx_1", index_top_n=5)
    )
    fq = _FakeQ([""])  # empty input → exit
    monkeypatch.setattr(chat_wf, "questionary", fq)
    settings = Settings()  # mode=mock by default
    chat_wf.run(client, settings, Console(), _preset={"agent_id": agent.id})


def test_chat_one_turn_then_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """One question → response rendered → empty exits."""
    store = Store()
    client = PaisClient(FakeTransport(store))
    agent = client.agents.create(
        AgentCreate(name="a1", model="openai/gpt-oss-120b-4x", index_id="idx_1", index_top_n=5)
    )
    fq = _FakeQ(["what is this?", ""])
    monkeypatch.setattr(chat_wf, "questionary", fq)
    chat_wf.run(client, Settings(), Console(), _preset={"agent_id": agent.id})
    # Both scripted answers consumed.
    assert fq.scripted == []


def test_chat_file_shortcut_loads_and_sends(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """`/file <path>` reads the file and uses its contents as the chat message."""
    store = Store()
    client = PaisClient(FakeTransport(store))
    agent = client.agents.create(
        AgentCreate(name="a1", model="openai/gpt-oss-120b-4x", index_id="idx_1", index_top_n=5)
    )
    prompt_file = tmp_path / "prompt.md"
    prompt_file.write_text("# Task\nSummarize the Access Management suite.\n", encoding="utf-8")

    captured: dict[str, Any] = {}
    real_chat = client.agents.chat

    def spy_chat(agent_id: str, request: Any) -> Any:
        captured["content"] = request.messages[0].content
        return real_chat(agent_id, request)

    monkeypatch.setattr(client.agents, "chat", spy_chat)

    fq = _FakeQ([f"/file {prompt_file}", ""])
    monkeypatch.setattr(chat_wf, "questionary", fq)
    chat_wf.run(client, Settings(), Console(), _preset={"agent_id": agent.id})

    # File contents become the user message verbatim (ignoring a possible trailing newline).
    assert captured["content"].rstrip("\n") == "# Task\nSummarize the Access Management suite."
    assert fq.scripted == []


def test_chat_file_shortcut_missing_file_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Missing path prints an error and loops; next empty input exits. No LLM call."""
    store = Store()
    client = PaisClient(FakeTransport(store))
    agent = client.agents.create(
        AgentCreate(name="a1", model="openai/gpt-oss-120b-4x", index_id="idx_1", index_top_n=5)
    )
    called = {"n": 0}
    real_chat = client.agents.chat

    def spy_chat(agent_id: str, request: Any) -> Any:
        called["n"] += 1
        return real_chat(agent_id, request)

    monkeypatch.setattr(client.agents, "chat", spy_chat)

    missing = tmp_path / "nope.md"
    fq = _FakeQ([f"/file {missing}", ""])
    monkeypatch.setattr(chat_wf, "questionary", fq)
    chat_wf.run(client, Settings(), Console(), _preset={"agent_id": agent.id})

    assert called["n"] == 0
    assert fq.scripted == []


def test_chat_error_is_saved_to_disk(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A failed chat turn writes a JSON dump under the errors dir and keeps the loop alive."""
    import json as _json

    from pais.cli import _error_dump
    from pais.errors import PaisServerError

    errors_dir = tmp_path / "chat-errors"
    monkeypatch.setattr(_error_dump, "_CHAT_ERRORS_DIR", errors_dir)

    store = Store()
    client = PaisClient(FakeTransport(store))
    agent = client.agents.create(
        AgentCreate(name="a1", model="openai/gpt-oss-120b-4x", index_id="idx_1", index_top_n=5)
    )

    def boom(*_a: Any, **_k: Any) -> Any:
        raise PaisServerError("server exploded", status_code=502, request_id="rid-chat-boom")

    monkeypatch.setattr(client.agents, "chat", boom)

    fq = _FakeQ(["first turn fails", ""])  # one try then empty exits
    monkeypatch.setattr(chat_wf, "questionary", fq)
    chat_wf.run(client, Settings(), Console(), _preset={"agent_id": agent.id})

    dumps = list(errors_dir.glob("*.json"))
    assert len(dumps) == 1
    data = _json.loads(dumps[0].read_text(encoding="utf-8"))
    assert data["status_code"] == 502
    assert data["request_id"] == "rid-chat-boom"
    assert data["prompt_excerpt"] == "first turn fails"
    assert data["agent_id"] == agent.id
    assert fq.scripted == []
