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
