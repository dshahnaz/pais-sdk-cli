"""Pickers: live KB/index lookups, alias merge, manual fallback, error path."""

from __future__ import annotations

from typing import Any

import pytest

from pais.cli import _alias, _pickers
from pais.cli._pickers import PickerContext, pick_index, pick_kb, pick_splitter_kind
from pais.client import PaisClient
from pais.errors import PaisServerError
from pais.models import IndexCreate, KnowledgeBaseCreate
from pais.transport.fake_transport import FakeTransport
from pais_mock.state import Store

# ----- fake questionary -------------------------------------------------------


class _FakeAsk:
    def __init__(self, value: Any) -> None:
        self._v = value

    def ask(self) -> Any:
        return self._v


class _FakeQuestionary:
    """Replaces `questionary.select` and `questionary.text` with scripted answers."""

    def __init__(self) -> None:
        self.scripted: list[Any] = []
        self.calls: list[dict[str, Any]] = []

    def script(self, *answers: Any) -> None:
        self.scripted = list(answers)

    def select(self, message: str, *, choices: list[Any], **_: Any) -> _FakeAsk:
        self.calls.append({"kind": "select", "message": message, "choices": choices})
        return _FakeAsk(self.scripted.pop(0))

    def text(self, message: str, **_: Any) -> _FakeAsk:
        self.calls.append({"kind": "text", "message": message})
        return _FakeAsk(self.scripted.pop(0))

    def confirm(self, message: str, **_: Any) -> _FakeAsk:
        self.calls.append({"kind": "confirm", "message": message})
        return _FakeAsk(self.scripted.pop(0))


@pytest.fixture
def fake_q(monkeypatch: pytest.MonkeyPatch) -> _FakeQuestionary:
    fq = _FakeQuestionary()
    monkeypatch.setattr(_pickers, "questionary", fq)
    return fq


@pytest.fixture
def isolated_cache(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(_alias, "CACHE_PATH", tmp_path / "aliases.json")


# ----- picker tests -----------------------------------------------------------


def test_pick_kb_returns_uuid_when_no_alias(fake_q: _FakeQuestionary, isolated_cache: None) -> None:
    """A KB without a TOML alias is picked by UUID."""
    store = Store()
    client = PaisClient(FakeTransport(store))
    kb = client.knowledge_bases.create(KnowledgeBaseCreate(name="server-only-kb"))
    ctx = PickerContext(client=client, answers={}, profile="default")

    # First call lists the KB choices; we pick the only KB title.
    titles_will_be: list[str] = []  # captured by side-effect

    def _select(message: str, *, choices: list[Any], **_: Any) -> _FakeAsk:
        titles_will_be.extend(choices)
        return _FakeAsk(choices[0])  # the KB row, not "✏ enter manually"

    fake_q.select = _select  # type: ignore[method-assign]
    result = pick_kb(ctx)
    assert result == kb.id
    assert any("server-only-kb" in t for t in titles_will_be)


def test_pick_kb_falls_back_to_text_on_server_error(
    fake_q: _FakeQuestionary, isolated_cache: None
) -> None:
    """If the server raises, the picker drops to a text() prompt."""

    class _BoomKbs:
        def list(self) -> Any:
            raise PaisServerError("simulated outage", status_code=500)

    client = PaisClient(FakeTransport(Store()))
    client.knowledge_bases = _BoomKbs()  # type: ignore[assignment]
    ctx = PickerContext(client=client, answers={}, profile="default")

    fake_q.script("typed-uuid-fallback")
    result = pick_kb(ctx)
    assert result == "typed-uuid-fallback"
    # Confirm a text() (not select()) prompt was shown.
    assert fake_q.calls[-1]["kind"] == "text"
    assert "server unreachable" in fake_q.calls[-1]["message"]


def test_pick_index_uses_previously_picked_kb(
    fake_q: _FakeQuestionary, isolated_cache: None
) -> None:
    """`pick_index` reads ctx.answers['kb_ref'] to scope the index list."""
    store = Store()
    client = PaisClient(FakeTransport(store))
    kb = client.knowledge_bases.create(KnowledgeBaseCreate(name="kb1"))
    client.indexes.create(
        kb.id,
        IndexCreate(name="ix-only", embeddings_model_endpoint="BAAI/bge-small-en-v1.5"),
    )
    ctx = PickerContext(client=client, answers={"kb_ref": kb.id}, profile="default")

    captured_choices: list[Any] = []

    def _select(message: str, *, choices: list[Any], **_: Any) -> _FakeAsk:
        captured_choices.extend(choices)
        return _FakeAsk(choices[0])  # the index row

    fake_q.select = _select  # type: ignore[method-assign]
    result = pick_index(ctx)
    assert any("ix-only" in t for t in captured_choices)
    # The picker returns the UUID since there's no TOML alias.
    assert result is not None and result != _pickers._MANUAL


def test_pick_splitter_kind_lists_registry(fake_q: _FakeQuestionary) -> None:
    ctx = PickerContext(client=PaisClient(FakeTransport(Store())), answers={}, profile="default")
    captured: list[str] = []

    def _select(message: str, *, choices: list[Any], **_: Any) -> _FakeAsk:
        captured.extend(choices)
        return _FakeAsk("test_suite_md")

    fake_q.select = _select  # type: ignore[method-assign]
    result = pick_splitter_kind(ctx)
    assert result == "test_suite_md"
    # All four built-in splitters appear.
    for kind in ("test_suite_md", "passthrough", "markdown_headings", "text_chunks"):
        assert kind in captured


def test_picker_for_dispatch_table() -> None:
    """The dispatch table maps the parameter names we care about."""
    from pais.cli._pickers import picker_for

    assert picker_for(("index", "delete"), "kb_ref") is not None
    assert picker_for(("index", "delete"), "index_ref") is not None
    assert picker_for(("agent", "chat"), "agent_id") is not None
    assert picker_for(("splitters", "show"), "kind") is not None
    assert picker_for(("alias", "clear"), "alias") is not None
    # Unknown param → no picker.
    assert picker_for(("kb", "create"), "name") is None
