"""Every picker exposes `← back`; selecting it returns CANCEL.
Ctrl-C path (questionary returns None) returns the same sentinel."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pais.cli import _alias, _pickers
from pais.cli._pickers import (
    PickerContext,
    pick_agent,
    pick_index,
    pick_kb,
    pick_or_create_agent,
    pick_or_create_index,
    pick_or_create_kb,
    pick_or_create_splitter_config,
    pick_splitter_kind,
)
from pais.cli._prompts import CANCEL
from pais.client import PaisClient
from pais.models import IndexCreate, KnowledgeBaseCreate
from pais.transport.fake_transport import FakeTransport
from pais_mock.state import Store


class _FakeAsk:
    def __init__(self, value: Any) -> None:
        self._v = value

    def ask(self) -> Any:
        return self._v


class _FakeQ:
    """Captures the last `select` call and returns a scripted answer."""

    def __init__(self) -> None:
        self.last_choices: list[Any] | None = None
        self.last_instruction: str | None = None
        self.scripted: Any = None

    def select(self, message: str, *, choices: list[Any], **kw: Any) -> _FakeAsk:
        self.last_choices = list(choices)
        self.last_instruction = kw.get("instruction")
        return _FakeAsk(self.scripted)

    def text(self, *_a: Any, **_k: Any) -> _FakeAsk:
        return _FakeAsk(self.scripted)

    def confirm(self, *_a: Any, **_k: Any) -> _FakeAsk:
        return _FakeAsk(self.scripted)


@pytest.fixture
def fq(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _FakeQ:
    fq_ = _FakeQ()
    monkeypatch.setattr(_pickers, "questionary", fq_)
    monkeypatch.setattr(_alias, "CACHE_PATH", tmp_path / "aliases.json")
    return fq_


def _seed_kb_index() -> tuple[PaisClient, str]:
    store = Store()
    c = PaisClient(FakeTransport(store))
    kb = c.knowledge_bases.create(KnowledgeBaseCreate(name="kb1"))
    c.indexes.create(
        kb.id, IndexCreate(name="ix1", embeddings_model_endpoint="BAAI/bge-small-en-v1.5")
    )
    return c, kb.id


# ----- Every picker exposes ← back -------------------------------------------


def test_pick_kb_offers_back_row(fq: _FakeQ) -> None:
    client, _ = _seed_kb_index()
    ctx = PickerContext(client=client, answers={}, profile="default")
    fq.scripted = "←  back"
    result = pick_kb(ctx)
    assert result is CANCEL
    assert "←  back" in (fq.last_choices or [])
    assert fq.last_instruction is not None and "back" in fq.last_instruction.lower()


def test_pick_index_offers_back_row(fq: _FakeQ) -> None:
    client, kb_uuid = _seed_kb_index()
    ctx = PickerContext(client=client, answers={"kb_ref": kb_uuid}, profile="default")
    fq.scripted = "←  back"
    result = pick_index(ctx)
    assert result is CANCEL
    assert "←  back" in (fq.last_choices or [])


def test_pick_agent_offers_back_row(fq: _FakeQ) -> None:
    from pais.models import AgentCreate

    client, _ = _seed_kb_index()
    client.agents.create(AgentCreate(name="a1", model="openai/gpt-oss-120b-4x"))
    ctx = PickerContext(client=client, answers={}, profile="default")
    fq.scripted = "←  back"
    result = pick_agent(ctx)
    assert result is CANCEL
    assert "←  back" in (fq.last_choices or [])


def test_pick_splitter_kind_offers_back_row(fq: _FakeQ) -> None:
    client, _ = _seed_kb_index()
    ctx = PickerContext(client=client, answers={}, profile="default")
    fq.scripted = "←  back"
    result = pick_splitter_kind(ctx)
    assert result is CANCEL
    assert "←  back" in (fq.last_choices or [])


def test_pick_or_create_kb_offers_back_row(fq: _FakeQ) -> None:
    client, _ = _seed_kb_index()
    ctx = PickerContext(client=client, answers={}, profile="default")
    fq.scripted = "←  back"
    result = pick_or_create_kb(ctx)
    assert result is CANCEL
    assert "←  back" in (fq.last_choices or [])


def test_pick_or_create_index_offers_back_row(fq: _FakeQ) -> None:
    client, kb_uuid = _seed_kb_index()
    ctx = PickerContext(client=client, answers={"kb_ref": kb_uuid}, profile="default")
    fq.scripted = "←  back"
    result = pick_or_create_index(ctx)
    assert result is CANCEL
    assert "←  back" in (fq.last_choices or [])


def test_pick_or_create_agent_offers_back_row(fq: _FakeQ) -> None:
    client, _ = _seed_kb_index()
    ctx = PickerContext(client=client, answers={}, profile="default")
    fq.scripted = "←  back"
    result = pick_or_create_agent(ctx)
    assert result is CANCEL
    assert "←  back" in (fq.last_choices or [])


def test_pick_or_create_splitter_config_offers_back_row(fq: _FakeQ) -> None:
    client, _ = _seed_kb_index()
    ctx = PickerContext(client=client, answers={}, profile="default")
    fq.scripted = "←  back"
    result = pick_or_create_splitter_config(ctx)
    assert result is CANCEL
    assert "←  back" in (fq.last_choices or [])


# ----- Ctrl-C path returns the same sentinel ---------------------------------


def test_ctrl_c_returns_cancel_in_pick_kb(fq: _FakeQ) -> None:
    """questionary returns None on Ctrl-C; picker maps that to CANCEL."""
    client, _ = _seed_kb_index()
    ctx = PickerContext(client=client, answers={}, profile="default")
    fq.scripted = None  # the Ctrl-C signal
    result = pick_kb(ctx)
    assert result is CANCEL


def test_back_hint_present_in_select_instruction(fq: _FakeQ) -> None:
    """Every picker passes an `instruction=` hint mentioning back navigation."""
    client, _ = _seed_kb_index()
    ctx = PickerContext(client=client, answers={}, profile="default")
    fq.scripted = "←  back"
    pick_kb(ctx)
    assert fq.last_instruction is not None
    assert "back" in fq.last_instruction.lower()
