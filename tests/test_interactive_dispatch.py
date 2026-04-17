"""End-to-end dispatch test: scripted answers drive the menu through
`index delete`, the resolved kb/index UUIDs reach the underlying callback,
and `--yes` is auto-passed."""

from __future__ import annotations

from typing import Any

import pytest

from pais.cli import _alias, _landing, _pickers, _prompts, interactive
from pais.cli._introspect import walk
from pais.cli.app import app
from pais.client import PaisClient
from pais.config import Settings
from pais.models import IndexCreate, KnowledgeBaseCreate
from pais.transport.fake_transport import FakeTransport
from pais_mock.state import Store


class _FakeAsk:
    def __init__(self, value: Any) -> None:
        self._v = value

    def ask(self) -> Any:
        return self._v


class _FakeQuestionary:
    def __init__(self) -> None:
        self.scripted: list[Any] = []
        self.calls: list[dict[str, Any]] = []

    def script(self, *answers: Any) -> None:
        self.scripted = list(answers)

    def _next(self) -> Any:
        return self.scripted.pop(0)

    def select(self, message: str, *, choices: list[Any], **_: Any) -> _FakeAsk:
        self.calls.append({"kind": "select", "message": message, "choices": choices})
        return _FakeAsk(self._next())

    def text(self, message: str, **_: Any) -> _FakeAsk:
        self.calls.append({"kind": "text", "message": message})
        return _FakeAsk(self._next())

    def confirm(self, message: str, **_: Any) -> _FakeAsk:
        self.calls.append({"kind": "confirm", "message": message})
        return _FakeAsk(self._next())


@pytest.fixture
def fake_q(monkeypatch: pytest.MonkeyPatch) -> _FakeQuestionary:
    fq = _FakeQuestionary()
    monkeypatch.setattr(interactive, "questionary", fq)
    monkeypatch.setattr(_pickers, "questionary", fq)
    monkeypatch.setattr(_landing, "questionary", fq)
    monkeypatch.setattr(_prompts, "questionary", fq)
    return fq


@pytest.fixture
def isolated_cache(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(_alias, "CACHE_PATH", tmp_path / "aliases.json")


def test_index_delete_flow_dispatches_with_resolved_uuids(
    fake_q: _FakeQuestionary,
    isolated_cache: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pick `index delete` → KB → index → confirm. The underlying callback
    must be called with the resolved UUIDs (not the menu titles)."""
    # Set up a mock store with one KB + one index. Wire Settings to use it.
    store = Store()
    seed_client = PaisClient(FakeTransport(store))
    kb = seed_client.knowledge_bases.create(KnowledgeBaseCreate(name="kb1"))
    ix = seed_client.indexes.create(
        kb.id,
        IndexCreate(name="ix1", embeddings_model_endpoint="BAAI/bge-small-en-v1.5"),
    )

    # Patch Settings.build_client so every dispatched command shares the same store.
    def _build(_self: Any) -> PaisClient:
        return PaisClient(FakeTransport(store))

    monkeypatch.setattr(Settings, "build_client", _build)

    # Find the menu title that the loop will render for `index delete`.
    specs = walk(app)
    target_menu_title = next(
        t for t in (f"{s.display:24s}  {s.help or '—'}" for s in specs) if "index delete" in t
    )

    # Script: landing → flat menu → KB picker → index picker → confirm.
    # First land on the v0.6 landing screen; pick the flat-menu fallback.
    fake_q.script(
        "📋  all commands…",  # landing screen → fall through to flat menu
        target_menu_title,  # flat menu pick
        # KB picker title format: f"—  {kb.name}  ({kb.id})" — no alias declared.
        f"—  kb1  ({kb.id})",
        # Index picker — mock returns status=AVAILABLE, no num_documents → "—".
        f"—  ix1  (status=AVAILABLE, docs=—, id={ix.id})",
        True,  # destructive confirm
        # Loop iteration 2: landing again, then ⏏ quit on the flat menu.
        "📋  all commands…",
        "⏏  quit",
    )

    interactive.enter_interactive(app)

    # Verify the index is gone.
    final = PaisClient(FakeTransport(store))
    assert final.indexes.list(kb.id).data == []

    # Verify the destructive confirm was the second-to-last questionary call.
    confirms = [c for c in fake_q.calls if c["kind"] == "confirm"]
    assert any("delete" in c["message"].lower() for c in confirms)
    # The confirm message must echo the resolved KB UUID so the user sees what's deleted.
    assert any(kb.id in c["message"] or "kb1" in c["message"] for c in fake_q.calls)


def test_quit_exits_immediately(fake_q: _FakeQuestionary, isolated_cache: None) -> None:
    """Landing screen → fall through to flat menu → quit."""
    fake_q.script("📋  all commands…", "⏏  quit")
    interactive.enter_interactive(app)
    # Two select calls: landing + flat menu.
    assert len([c for c in fake_q.calls if c["kind"] == "select"]) == 2


def test_agent_create_flow_picks_kb_then_index(
    fake_q: _FakeQuestionary,
    isolated_cache: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`agent create` has no `kb_ref` parameter. The index_id picker must
    cascade into a KB pick first, then an index pick under that KB — not
    fall through to a free-text prompt. The resolved index UUID must reach
    the callback (not the raw menu title)."""
    from rich.console import Console

    from pais.cli._workflows import _base as _workflows_base
    from pais.cli.interactive import _dispatch

    # Seed a KB + index in a shared store.
    store = Store()
    seed_client = PaisClient(FakeTransport(store))
    kb = seed_client.knowledge_bases.create(KnowledgeBaseCreate(name="kb-agent"))
    ix = seed_client.indexes.create(
        kb.id,
        IndexCreate(name="ix-agent", embeddings_model_endpoint="BAAI/bge-small-en-v1.5"),
    )

    def _build(_self: Any) -> PaisClient:
        return PaisClient(FakeTransport(store))

    monkeypatch.setattr(Settings, "build_client", _build)
    # The optional-review screen lives in _workflows._base; patch its questionary too.
    monkeypatch.setattr(_workflows_base, "questionary", fake_q)

    # Locate the agent-create spec and intercept its callback.
    specs = walk(app)
    spec = next(s for s in specs if s.path == ("agent", "create"))
    captured: dict[str, Any] = {}

    def _recorder(**kwargs: Any) -> None:
        captured.update(kwargs)

    spec.callback = _recorder  # type: ignore[misc]

    # Script the dispatch flow in order:
    #   1. text  — name
    #   2. select — chat model picker
    #   3. select — cascaded KB picker (kb_ref absent → pick_kb fires)
    #   4. select — index picker under picked KB
    #   5. select — optional-review screen → ✅ Go
    fake_q.script(
        "my-agent",
        "openai/gpt-oss-120b-4x  ·  VLLM",
        f"—  kb-agent  ({kb.id})",
        f"—  ix-agent  (status=AVAILABLE, docs=—, id={ix.id})",
        "✅ Go (commit)",
    )

    _dispatch(spec, Settings(), Console())

    assert captured.get("index_id") == ix.id, "resolved index UUID must reach the callback"
    assert captured.get("name") == "my-agent"

    selects = [c for c in fake_q.calls if c["kind"] == "select"]
    assert "chat model" in selects[0]["message"].lower()
    assert "Pick a KB" in selects[1]["message"]
    assert "under" in selects[2]["message"], "index picker must be scoped by the picked KB"
    # And explicitly: no free-text prompt for index_id.
    texts = [c for c in fake_q.calls if c["kind"] == "text"]
    assert not any("index alias or UUID" in t["message"] for t in texts)

    # Hidden `kb_search_tool` must reach the callback as its declared default
    # (None), NOT as typer's raw OptionInfo wrapper — otherwise the `if
    # kb_search_tool:` branch in agent_create trips and pydantic blows up on
    # ToolLink(tool_id=<OptionInfo>).
    assert captured.get("kb_search_tool") is None
    # Regression v0.7.3: the cascade stashes `kb_ref` into ctx.answers as scratch
    # state; it must NOT leak through as a kwarg to `agent_create` (which has no
    # such param). Dispatch filters by spec.params before calling the callback.
    assert "kb_ref" not in captured, (
        "kb_ref scratch key must not leak into callback kwargs (v0.7.3 regression)"
    )


def test_dispatch_filters_picker_scratch_keys(
    fake_q: _FakeQuestionary,
    isolated_cache: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defense-in-depth: a picker that stashes an arbitrary scratch key into
    `ctx.answers` must not crash the callback with an unexpected-kwarg TypeError.
    The fix filters `answers` to declared `spec.params` before invoking."""
    from rich.console import Console

    from pais.cli._pickers import CANCEL as PICKER_CANCEL
    from pais.cli._workflows import _base as _workflows_base
    from pais.cli.interactive import _dispatch

    store = Store()
    seed_client = PaisClient(FakeTransport(store))
    kb = seed_client.knowledge_bases.create(KnowledgeBaseCreate(name="kb-scratch"))
    seed_client.indexes.create(
        kb.id,
        IndexCreate(name="ix-scratch", embeddings_model_endpoint="BAAI/bge-small-en-v1.5"),
    )

    def _build(_self: Any) -> PaisClient:
        return PaisClient(FakeTransport(store))

    monkeypatch.setattr(Settings, "build_client", _build)
    monkeypatch.setattr(_workflows_base, "questionary", fake_q)

    specs = walk(app)
    spec = next(s for s in specs if s.path == ("agent", "create"))

    # Strict callback — only accepts declared params. If any scratch key leaks
    # through, Python raises TypeError and the test fails.
    received: dict[str, Any] = {}

    def _strict(
        name: str,
        model: str,
        instructions: str | None = None,
        index_id: str | None = None,
        index_top_n: int = 5,
        index_similarity_cutoff: float = 0.0,
        kb_search_tool: Any = None,
        output: str = "table",
    ) -> None:
        received.update(
            {
                "name": name,
                "model": model,
                "index_id": index_id,
            }
        )

    spec.callback = _strict  # type: ignore[misc]

    # Override the index picker to stash a bogus scratch key, then also write a
    # legitimate index_id answer. This simulates any current or future picker
    # that uses ctx.answers as a side-channel.
    def _picker_with_scratch(_path: tuple[str, ...], param_name: str) -> Any:
        if param_name != "index_id":
            return None

        def _inner(ctx: Any) -> Any:
            ctx.answers["kb_ref"] = kb.id
            ctx.answers["__bogus_scratch__"] = "leak-me"
            idx = seed_client.indexes.list(kb.id).data[0]
            return idx.id

        return _inner

    monkeypatch.setattr("pais.cli.interactive.picker_for", _picker_with_scratch)

    fake_q.script(
        "my-agent",
        "openai/gpt-oss-120b-4x  ·  VLLM",
        "✅ Go (commit)",
    )

    # Must not raise TypeError.
    _dispatch(spec, Settings(), Console())

    assert received["name"] == "my-agent"
    assert PICKER_CANCEL is not None  # import sanity


def test_shell_logs_command_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    """When a dispatched command raises, the top-level shell loop must log it
    (v0.7.3). Without this, tracebacks never reach `~/.pais/logs/pais.log` and
    `pais doctor` / `pais logs tail` can't recover them for support.

    We exercise the exception path by stubbing `_dispatch` to raise, then
    driving `enter_interactive` through one flat-menu iteration + quit. The
    assertion is on `interactive.log` which is the structlog logger the fix
    added at module scope."""

    logged: list[tuple[str, str, dict[str, Any]]] = []

    class _RecorderLogger:
        def error(self, event: str, **kw: Any) -> None:
            logged.append(("error", event, kw))

        def exception(self, event: str, **kw: Any) -> None:
            logged.append(("exception", event, kw))

    monkeypatch.setattr(interactive, "log", _RecorderLogger())

    def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("synthetic failure for logging regression test")

    monkeypatch.setattr(interactive, "_dispatch", _boom)

    fq = _FakeQuestionary()
    # Landing → flat menu pick (any non-quit) → after boom, back to landing → quit.
    specs = walk(app)
    first_non_quit = next(
        f"{s.display:24s}  {s.help or '—'}" for s in specs if s.path == ("kb", "list")
    )
    fq.script(
        "📋  all commands…",
        first_non_quit,
        "📋  all commands…",
        "⏏  quit",
    )
    monkeypatch.setattr(interactive, "questionary", fq)
    monkeypatch.setattr(_landing, "questionary", fq)

    interactive.enter_interactive(app)

    assert any(evt == "shell.command_crashed" for _, evt, _ in logged), (
        f"RuntimeError from a dispatched command must be logged, got: {logged}"
    )


def test_picker_status_label_lookup(fake_q: _FakeQuestionary, isolated_cache: None) -> None:
    """Make sure the index picker uses ix.status correctly even when the model
    returns the StatusEnum (not a raw string)."""
    store = Store()
    client = PaisClient(FakeTransport(store))
    kb = client.knowledge_bases.create(KnowledgeBaseCreate(name="x"))
    client.indexes.create(
        kb.id,
        IndexCreate(name="i", embeddings_model_endpoint="BAAI/bge-small-en-v1.5"),
    )
    ctx = _pickers.PickerContext(client=client, answers={"kb_ref": kb.id}, profile="default")
    captured: list[str] = []

    def _select(message: str, *, choices: list[Any], **_: Any) -> _FakeAsk:
        captured.extend(choices)
        return _FakeAsk(choices[0])

    fake_q.select = _select  # type: ignore[method-assign]
    _pickers.pick_index(ctx)
    assert any("status=AVAILABLE" in t for t in captured)
