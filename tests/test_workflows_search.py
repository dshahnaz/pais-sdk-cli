"""Search workflow — v0.6.6 removed the 'Customize top_n / similarity_cutoff?'
gate. After picking kb + index + query, the user must see a review screen
with defaults pre-filled; 'Go' runs the search with those defaults and zero
typed-in numeric values."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from pais.cli import _alias, _pickers, _recent
from pais.cli._workflows import _base as base_wf
from pais.cli._workflows import search as search_wf
from pais.client import PaisClient
from pais.config import Settings
from pais.models import IndexCreate, KnowledgeBaseCreate
from pais.transport.fake_transport import FakeTransport
from pais_mock.state import Store


class _Ask:
    def __init__(self, value: Any) -> None:
        self._v = value

    def ask(self) -> Any:
        return self._v


class _FakeQ:
    """Records every call by widget type + argument; returns the next scripted answer."""

    def __init__(self) -> None:
        self.scripted: list[Any] = []
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def script(self, *answers: Any) -> None:
        self.scripted = list(answers)

    def _next(self) -> Any:
        if not self.scripted:
            raise AssertionError("FakeQ ran out of scripted answers")
        return self.scripted.pop(0)

    def select(self, *a: Any, **k: Any) -> _Ask:
        self.calls.append(("select", a, k))
        return _Ask(self._next())

    def text(self, *a: Any, **k: Any) -> _Ask:
        self.calls.append(("text", a, k))
        return _Ask(self._next())

    def confirm(self, *a: Any, **k: Any) -> _Ask:
        self.calls.append(("confirm", a, k))
        return _Ask(self._next())


@pytest.fixture
def fq(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _FakeQ:
    fq_ = _FakeQ()
    monkeypatch.setattr(search_wf, "questionary", fq_)
    monkeypatch.setattr(_pickers, "questionary", fq_)
    monkeypatch.setattr(base_wf, "questionary", fq_)
    monkeypatch.setattr(_alias, "CACHE_PATH", tmp_path / "aliases.json")
    monkeypatch.setattr(_recent, "CACHE_PATH", tmp_path / "recent.json")
    return fq_


def _seed_kb_with_doc(client: PaisClient) -> tuple[str, str, str]:
    kb = client.knowledge_bases.create(KnowledgeBaseCreate(name="kb"))
    ix = client.indexes.create(kb.id, IndexCreate(name="ix", embeddings_model_endpoint="bge"))
    # Upload one doc so the search returns at least one hit.
    client._transport.request(
        "POST",
        f"/control/knowledge-bases/{kb.id}/indexes/{ix.id}/documents",
        files={"file": ("seed.md", io.BytesIO(b"hello world foo bar baz"), "text/markdown")},
    )
    return kb.id, ix.id, "hello"


def test_search_has_no_customize_gate_defaults_used(fq: _FakeQ) -> None:
    """Script the minimum flow: pick KB → pick index → type query → 'Go'.
    No confirm() prompt may fire at any point; top_n=5 / cutoff=0.0 defaults must
    reach SearchQuery without the user being asked to opt-in."""
    store = Store()
    c = PaisClient(FakeTransport(store))
    kb_id, ix_id, query = _seed_kb_with_doc(c)

    # 1) pick_kb.select → the only KB title
    # 2) pick_index.select → the only index title
    # 3) query text
    # 4) review_screen select → "✅ Go (commit)"
    fq.script(
        f"—  kb  ({kb_id})",
        f"—  ix  (status=AVAILABLE, docs=—, id={ix_id})",
        query,
        "✅ Go (commit)",
    )

    settings = Settings()
    console = Console(record=True)

    search_wf.run(c, settings, console)

    # Assert no confirm() prompts — the customize gate is gone.
    confirm_calls = [c for c in fq.calls if c[0] == "confirm"]
    assert confirm_calls == [], f"unexpected confirm prompts: {confirm_calls}"

    # And sanity-check the review screen rendered with the two fields.
    rendered = console.export_text()
    assert "top_n" in rendered
    assert "similarity_cutoff" in rendered
