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


def test_kb_cleanup_typo_shows_visible_red_message(
    fq: _FakeQ, capsys: pytest.CaptureFixture[str]
) -> None:
    """v0.6.4: typo on confirm-by-typing must NOT be a dim aborted line."""
    store = Store()
    client = PaisClient(FakeTransport(store))
    kb = client.knowledge_bases.create(KnowledgeBaseCreate(name="precious_kb"))
    fq.script(
        "KB (cascades indexes + docs)",
        f"—  precious_kb  ({kb.id})",
        "typo",
    )
    # Pipe Rich's output to a force_terminal=False console so the captured
    # text is plain ASCII; we only check the visible content (not ANSI).
    cleanup_wf.run(client, Settings(), Console(force_terminal=False, no_color=True))
    captured = capsys.readouterr().out
    # Visible message, not the old [dim]aborted[/dim].
    assert "didn't match" in captured
    # KB still there.
    assert any(k.name == "precious_kb" for k in client.knowledge_bases.list().data)


def test_kb_cleanup_verifies_deletion_after_call(
    fq: _FakeQ, capsys: pytest.CaptureFixture[str]
) -> None:
    """v0.6.4: green ✓ banner only when re-fetch confirms the resource is gone."""
    store = Store()
    client = PaisClient(FakeTransport(store))
    kb = client.knowledge_bases.create(KnowledgeBaseCreate(name="real_kb"))
    fq.script(
        "KB (cascades indexes + docs)",
        f"—  real_kb  ({kb.id})",
        "real_kb",
    )
    cleanup_wf.run(client, Settings(), Console(force_terminal=False, no_color=True))
    out = capsys.readouterr().out
    # Verified-gone path → green ✓ banner
    assert "real_kb" in out
    assert "✓" in out  # not the red ✗
    assert "still lists it" not in out


def test_index_cleanup_unsupported_offers_alternatives(
    fq: _FakeQ, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """v0.6.4: when the SDK raises IndexDeleteUnsupported, workflow shows the
    alternatives picker (no green ✓)."""
    from pais.errors import IndexDeleteUnsupported
    from pais.models import IndexCreate

    store = Store()
    client = PaisClient(FakeTransport(store))
    kb = client.knowledge_bases.create(KnowledgeBaseCreate(name="kb_x"))
    ix = client.indexes.create(
        kb.id, IndexCreate(name="ix_x", embeddings_model_endpoint="BAAI/bge-small-en-v1.5")
    )

    # Force indexes.delete to raise IndexDeleteUnsupported.
    def _boom(*_a: Any, **_k: Any) -> None:
        raise IndexDeleteUnsupported()

    monkeypatch.setattr(client.indexes, "delete", _boom)

    fq.script(
        "Index (cascades docs)",
        f"—  kb_x  ({kb.id})",  # KB picker
        f"—  ix_x  (status=AVAILABLE, docs=—, id={ix.id})",  # index picker
        "ix_x",  # type-to-confirm
        "← back",  # alternatives picker → cancel
    )
    cleanup_wf.run(client, Settings(), Console(force_terminal=False, no_color=True))
    out = capsys.readouterr().out
    # Red banner instead of green ✓
    assert "Index DELETE not supported" in out or "not supported" in out
    # Index still there.
    assert any(i.name == "ix_x" for i in client.indexes.list(kb.id).data)
