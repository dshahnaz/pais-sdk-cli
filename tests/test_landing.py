"""Smart landing screen: env snapshot, suggestion, and pick routing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from pais.cli import _alias, _landing
from pais.client import PaisClient
from pais.config import Settings
from pais.models import AgentCreate, IndexCreate, KnowledgeBaseCreate
from pais.transport.fake_transport import FakeTransport
from pais_mock.state import Store


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(_alias, "CACHE_PATH", tmp_path / "aliases.json")


def test_gather_counts_kbs_indexes_agents() -> None:
    store = Store()
    client = PaisClient(FakeTransport(store))
    kb = client.knowledge_bases.create(KnowledgeBaseCreate(name="k1"))
    client.indexes.create(
        kb.id, IndexCreate(name="i1", embeddings_model_endpoint="BAAI/bge-small-en-v1.5")
    )
    client.agents.create(AgentCreate(name="a1", model="openai/gpt-oss-120b-4x", index_id="x"))
    snap = _landing.gather(client, "default")
    assert snap.kb_count == 1
    assert snap.index_count == 1
    assert snap.agent_count == 1
    assert snap.drift_count == 0
    assert snap.error is None


def test_suggest_picks_setup_agent_when_no_agents() -> None:
    snap = _landing.EnvSnapshot(kb_count=2, index_count=2, agent_count=0, drift_count=0)
    assert _landing.suggest(snap).name == "Set up a chat agent over my docs"


def test_suggest_picks_chat_when_agents_and_indexes_exist() -> None:
    snap = _landing.EnvSnapshot(kb_count=1, index_count=1, agent_count=1, drift_count=0)
    assert _landing.suggest(snap).name == "Chat with an agent"


def test_suggest_picks_apply_toml_when_drift() -> None:
    snap = _landing.EnvSnapshot(kb_count=0, index_count=0, agent_count=0, drift_count=1)
    assert _landing.suggest(snap).name == "Apply pending TOML config"


class _FakeAsk:
    def __init__(self, value: Any) -> None:
        self._v = value

    def ask(self) -> Any:
        return self._v


class _FakeQ:
    def __init__(self, scripted: list[Any]) -> None:
        self.scripted = list(scripted)
        self.calls: list[dict[str, Any]] = []

    def select(self, message: str, *, choices: list[Any], **_: Any) -> _FakeAsk:
        self.calls.append({"kind": "select", "message": message, "choices": choices})
        return _FakeAsk(self.scripted.pop(0))


def test_show_landing_returns_chosen_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    fq = _FakeQ(["→ 🤖  Set up a chat agent over my docs"])
    monkeypatch.setattr(_landing, "questionary", fq)
    client = PaisClient(FakeTransport(Store()))
    wf = _landing.show_landing(client, Settings(), Console())
    assert wf is not None
    assert wf.name == "Set up a chat agent over my docs"


def test_show_landing_returns_none_for_flat_menu_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fq = _FakeQ(["📋  all commands…"])
    monkeypatch.setattr(_landing, "questionary", fq)
    client = PaisClient(FakeTransport(Store()))
    wf = _landing.show_landing(client, Settings(), Console())
    assert wf is None
