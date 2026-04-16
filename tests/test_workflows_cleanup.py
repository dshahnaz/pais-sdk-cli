"""Workflow G (cleanup): type-to-confirm gates the destructive op."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from pais.cli import _alias, _pickers, _recent
from pais.cli._workflows import cleanup as cleanup_wf
from pais.client import PaisClient
from pais.config import Settings
from pais.models import KnowledgeBaseCreate
from pais.transport.fake_transport import FakeTransport
from pais_mock.state import Store


class _FakeAsk:
    def __init__(self, value: Any) -> None:
        self._v = value

    def ask(self) -> Any:
        return self._v


class _FakeQ:
    def __init__(self) -> None:
        self.scripted: list[Any] = []

    def script(self, *answers: Any) -> None:
        self.scripted = list(answers)

    def select(self, *_a: Any, **_k: Any) -> _FakeAsk:
        return _FakeAsk(self.scripted.pop(0))

    def text(self, *_a: Any, **_k: Any) -> _FakeAsk:
        return _FakeAsk(self.scripted.pop(0))

    def confirm(self, *_a: Any, **_k: Any) -> _FakeAsk:
        return _FakeAsk(self.scripted.pop(0))


@pytest.fixture
def fq(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _FakeQ:
    fq_ = _FakeQ()
    monkeypatch.setattr(cleanup_wf, "questionary", fq_)
    monkeypatch.setattr(_pickers, "questionary", fq_)
    # Also patch the questionary inside _base.confirm_by_typing.
    from pais.cli._workflows import _base

    monkeypatch.setattr(_base, "questionary", fq_)
    monkeypatch.setattr(_alias, "CACHE_PATH", tmp_path / "aliases.json")
    monkeypatch.setattr(_recent, "CACHE_PATH", tmp_path / "recent.json")
    return fq_


def test_kb_cleanup_with_correct_name_proceeds(fq: _FakeQ) -> None:
    store = Store()
    client = PaisClient(FakeTransport(store))
    kb = client.knowledge_bases.create(KnowledgeBaseCreate(name="kb_to_remove"))
    fq.script(
        "KB (cascades indexes + docs)",  # kind
        f"—  kb_to_remove  ({kb.id})",  # KB picker (no recents, no alias)
        "kb_to_remove",  # type-to-confirm with the exact name
    )
    cleanup_wf.run(client, Settings(), Console())
    # KB should be gone.
    assert all(k.name != "kb_to_remove" for k in client.knowledge_bases.list().data)


def test_kb_cleanup_with_wrong_name_aborts(fq: _FakeQ) -> None:
    store = Store()
    client = PaisClient(FakeTransport(store))
    kb = client.knowledge_bases.create(KnowledgeBaseCreate(name="precious_kb"))
    fq.script(
        "KB (cascades indexes + docs)",
        f"—  precious_kb  ({kb.id})",
        "wrong_name",  # mismatch → cancel
    )
    cleanup_wf.run(client, Settings(), Console())
    # KB still there.
    assert any(k.name == "precious_kb" for k in client.knowledge_bases.list().data)


def test_quick_confirm_env_var_falls_back_to_yn(
    fq: _FakeQ, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With PAIS_QUICK_CONFIRM=1, the type-to-confirm prompt becomes y/N."""
    monkeypatch.setenv("PAIS_QUICK_CONFIRM", "1")
    store = Store()
    client = PaisClient(FakeTransport(store))
    kb = client.knowledge_bases.create(KnowledgeBaseCreate(name="kb_q"))
    fq.script(
        "KB (cascades indexes + docs)",
        f"—  kb_q  ({kb.id})",
        True,  # confirm.ask() returns True
    )
    cleanup_wf.run(client, Settings(), Console())
    assert all(k.name != "kb_q" for k in client.knowledge_bases.list().data)
