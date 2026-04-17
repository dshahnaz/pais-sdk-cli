"""Pickers: live KB/index lookups, alias merge, manual fallback, error path."""

from __future__ import annotations

from typing import Any

import pytest

from pais.cli import _alias, _pickers
from pais.cli._pickers import PickerContext, pick_index, pick_kb, pick_splitter_kind
from pais.cli._prompts import CANCEL
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
        return _FakeAsk("test_suite_bge")

    fake_q.select = _select  # type: ignore[method-assign]
    result = pick_splitter_kind(ctx)
    assert result == "test_suite_bge"
    # Both built-in splitters appear.
    for kind in ("test_suite_bge", "test_suite_arctic"):
        assert kind in captured


def test_picker_for_dispatch_table() -> None:
    """The dispatch table maps the parameter names we care about."""
    from pais.cli._pickers import picker_for

    assert picker_for(("index", "delete"), "kb_ref") is not None
    assert picker_for(("index", "delete"), "index_ref") is not None
    assert picker_for(("agent", "chat"), "agent_id") is not None
    assert picker_for(("splitters", "show"), "kind") is not None
    assert picker_for(("alias", "clear"), "alias") is not None
    # Model pickers (v0.6.8).
    assert picker_for(("index", "create"), "embeddings_model") is not None
    assert picker_for(("agent", "create"), "model") is not None
    # v0.7.1: `agent create` uses `index_id` (doc-aligned), not MCP tools.
    assert picker_for(("agent", "create"), "index_id") is not None
    assert picker_for(("agent", "create"), "kb_search_tool") is None
    # Unknown param → no picker.
    assert picker_for(("kb", "create"), "name") is None


# ----- model pickers (v0.6.8) ------------------------------------------------


def test_pick_embeddings_model_filters_by_type(fake_q: _FakeQuestionary) -> None:
    """The mock store seeds 1 EMBEDDINGS + 2 COMPLETIONS models; picker shows
    only the embeddings row."""
    from pais.cli._pickers import pick_embeddings_model

    client = PaisClient(FakeTransport(Store()))
    ctx = PickerContext(client=client, answers={}, profile="default")

    captured_choices: list[Any] = []

    def _select(message: str, *, choices: list[Any], **_: Any) -> _FakeAsk:
        captured_choices.extend(choices)
        return _FakeAsk(choices[0])

    fake_q.select = _select  # type: ignore[method-assign]
    result = pick_embeddings_model(ctx)
    assert result == "BAAI/bge-small-en-v1.5"
    # Only the embeddings model shows as a model row — no gpt-oss / llama-cpp.
    model_rows = [c for c in captured_choices if isinstance(c, str) and "·" in c]
    assert len(model_rows) == 1
    assert "BAAI/bge-small-en-v1.5" in model_rows[0]


def test_pick_chat_model_filters_by_type(fake_q: _FakeQuestionary) -> None:
    from pais.cli._pickers import pick_chat_model

    client = PaisClient(FakeTransport(Store()))
    ctx = PickerContext(client=client, answers={}, profile="default")

    captured_choices: list[Any] = []

    def _select(message: str, *, choices: list[Any], **_: Any) -> _FakeAsk:
        captured_choices.extend(choices)
        return _FakeAsk(choices[0])

    fake_q.select = _select  # type: ignore[method-assign]
    result = pick_chat_model(ctx)
    # The first COMPLETIONS model in the mock is gpt-oss.
    assert result == "openai/gpt-oss-120b-4x"
    model_rows = [c for c in captured_choices if isinstance(c, str) and "·" in c]
    assert len(model_rows) == 2  # gpt-oss + llama-cpp
    assert any("BAAI/bge-small-en-v1.5" not in r for r in model_rows)


def test_pick_model_falls_back_to_manual_when_list_empty(fake_q: _FakeQuestionary) -> None:
    """Empty server list → text-prompt fallback, not a crash."""
    from pais.cli._pickers import pick_embeddings_model

    class _EmptyModels:
        def list(self) -> Any:
            class _R:
                def __init__(self) -> None:
                    self.data: list[Any] = []

            return _R()

    client = PaisClient(FakeTransport(Store()))
    client.models = _EmptyModels()  # type: ignore[assignment]
    ctx = PickerContext(client=client, answers={}, profile="default")

    fake_q.script("manual-model-id")
    result = pick_embeddings_model(ctx)
    assert result == "manual-model-id"
    assert fake_q.calls[-1]["kind"] == "text"
    assert "no embeddings models" in fake_q.calls[-1]["message"]


def test_pick_model_falls_back_on_paiserror(fake_q: _FakeQuestionary) -> None:
    """Server error → same text-prompt fallback, picker never crashes."""
    from pais.cli._pickers import pick_chat_model

    class _BoomModels:
        def list(self) -> Any:
            raise PaisServerError("simulated outage", status_code=500)

    client = PaisClient(FakeTransport(Store()))
    client.models = _BoomModels()  # type: ignore[assignment]
    ctx = PickerContext(client=client, answers={}, profile="default")

    fake_q.script("typed-model-id")
    result = pick_chat_model(ctx)
    assert result == "typed-model-id"
    assert fake_q.calls[-1]["kind"] == "text"
    assert "could not list models" in fake_q.calls[-1]["message"]


def test_first_model_id_returns_first_matching_kind() -> None:
    from pais.cli._pickers import first_model_id

    client = PaisClient(FakeTransport(Store()))
    ctx = PickerContext(client=client, answers={}, profile="default")
    assert first_model_id(ctx, kind="EMBEDDINGS") == "BAAI/bge-small-en-v1.5"
    assert first_model_id(ctx, kind="COMPLETIONS") == "openai/gpt-oss-120b-4x"


def test_first_model_id_returns_none_on_error() -> None:
    from pais.cli._pickers import first_model_id

    class _BoomModels:
        def list(self) -> Any:
            raise PaisServerError("boom", status_code=503)

    client = PaisClient(FakeTransport(Store()))
    client.models = _BoomModels()  # type: ignore[assignment]
    ctx = PickerContext(client=client, answers={}, profile="default")
    assert first_model_id(ctx, kind="EMBEDDINGS") is None


# ----- KB→index cascade (v0.7.2) ---------------------------------------------


def test_pick_index_cascades_to_kb_pick_when_missing(
    fake_q: _FakeQuestionary, isolated_cache: None
) -> None:
    """No KB in scope (e.g. `agent create`) → cascade into pick_kb, then list
    indexes under the picked KB. User sees two select lists, not a text prompt."""
    store = Store()
    client = PaisClient(FakeTransport(store))
    kb = client.knowledge_bases.create(KnowledgeBaseCreate(name="kb-cascade"))
    ix = client.indexes.create(
        kb.id,
        IndexCreate(name="ix-cascade", embeddings_model_endpoint="BAAI/bge-small-en-v1.5"),
    )
    ctx = PickerContext(client=client, answers={}, profile="default")

    calls: list[tuple[str, list[Any]]] = []

    def _select(message: str, *, choices: list[Any], **_: Any) -> _FakeAsk:
        calls.append((message, list(choices)))
        return _FakeAsk(choices[0])

    fake_q.select = _select  # type: ignore[method-assign]
    result = pick_index(ctx)

    assert len(calls) == 2, "expected KB picker then index picker"
    assert "Pick a KB" in calls[0][0]
    assert "under" in calls[1][0]
    assert ctx.answers["kb_ref"] == kb.id
    assert result == ix.id


def test_pick_index_cascade_cancel_propagates(
    fake_q: _FakeQuestionary, isolated_cache: None
) -> None:
    """If the user hits `← back` on the cascaded KB picker, the outer
    pick_index returns CANCEL and leaves ctx.answers untouched."""
    store = Store()
    client = PaisClient(FakeTransport(store))
    client.knowledge_bases.create(KnowledgeBaseCreate(name="kb-cancel"))
    ctx = PickerContext(client=client, answers={}, profile="default")

    def _select(message: str, *, choices: list[Any], **_: Any) -> _FakeAsk:
        return _FakeAsk(_pickers._BACK)

    fake_q.select = _select  # type: ignore[method-assign]
    result = pick_index(ctx)

    assert result is CANCEL
    assert "kb_ref" not in ctx.answers


def test_pick_or_create_index_cascades_create_new(
    fake_q: _FakeQuestionary, isolated_cache: None
) -> None:
    """pick_or_create_index → user picks `+ create new` from the cascaded KB
    picker → surface CREATE_NEW upward without attempting to list indexes
    under a non-existent KB."""
    store = Store()
    client = PaisClient(FakeTransport(store))
    client.knowledge_bases.create(KnowledgeBaseCreate(name="kb-create-cascade"))
    ctx = PickerContext(client=client, answers={}, profile="default")

    calls: list[str] = []

    def _select(message: str, *, choices: list[Any], **_: Any) -> _FakeAsk:
        calls.append(message)
        return _FakeAsk(_pickers._CREATE)

    fake_q.select = _select  # type: ignore[method-assign]
    result = _pickers.pick_or_create_index(ctx)

    assert result == _pickers.CREATE_NEW
    assert len(calls) == 1, "index list should never be attempted"
    assert "KB" in calls[0]
    assert "kb_ref" not in ctx.answers
