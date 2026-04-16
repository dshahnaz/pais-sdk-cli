"""Context-aware ref pickers for the interactive shell.

When the menu is about to prompt for a parameter like `kb_ref`, it consults
the dispatch table here. If a picker is registered, the user gets a `select`
list of live items (KBs, indexes, agents, splitter kinds, …) instead of a
free-form text prompt — answering the user's brief: *"if I run `index delete`,
show me the KBs I have, let me pick one, then show me the indexes under it,
let me pick one to remove."*

Pickers always include an "✏ enter manually" item so power users can paste
a UUID. On `PaisError` (server unreachable, auth failure) the picker falls
back to a `text()` prompt with a one-line warning so the menu never gets
stuck.

Pickers receive a `PickerContext` carrying the live `PaisClient` plus any
already-collected answers (so `pick_index` can use the previously-chosen
`kb_ref` to scope the index list).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import questionary

from pais.cli import _alias
from pais.cli._config_file import load_profile_config
from pais.cli._prompts import CANCEL
from pais.client import PaisClient
from pais.errors import PaisError
from pais.ingest.registry import SPLITTER_REGISTRY

_MANUAL = "✏  enter manually"


@dataclass
class PickerContext:
    """Carries everything a picker needs: live client + previously-answered params."""

    client: PaisClient
    answers: dict[str, Any]
    profile: str


# Picker = function (PickerContext) -> resolved-value | CANCEL
Picker = Callable[[PickerContext], Any]


# ----- individual pickers -----------------------------------------------------


def pick_kb(ctx: PickerContext) -> Any:
    """Choose a KB. Returns the alias if declared in TOML (so the resolver
    can hit cache), otherwise the UUID."""
    cfg, _, _ = load_profile_config()
    name_to_alias = {decl.name: alias for alias, decl in cfg.knowledge_bases.items()}
    try:
        kbs = ctx.client.knowledge_bases.list().data
    except PaisError as e:
        return _manual_fallback(f"server unreachable ({e}); type a KB alias or UUID:")

    if not kbs:
        return _manual_fallback("no KBs on the server; type an alias or UUID:")

    choices: list[Any] = []
    value_for_title: dict[str, str] = {}
    for kb in kbs:
        alias = name_to_alias.get(kb.name)
        if alias:
            title = f"{alias}  —  {kb.name}  ({kb.id})"
            value_for_title[title] = alias
        else:
            title = f"—  {kb.name}  ({kb.id})"
            value_for_title[title] = kb.id
        choices.append(title)
    choices.append(_MANUAL)

    pick = questionary.select("Pick a KB:", choices=choices).ask()
    if pick is None:
        return CANCEL
    if pick == _MANUAL:
        return _manual_fallback("type a KB alias or UUID:")
    return value_for_title[pick]


def pick_index(ctx: PickerContext) -> Any:
    """Choose an index under the previously-picked KB."""
    kb_ref = ctx.answers.get("kb_ref") or ctx.answers.get("kb_id")
    if not kb_ref:
        return _manual_fallback("kb_ref not yet chosen; type the index alias or UUID:")
    cfg, _, _ = load_profile_config()
    try:
        kb_uuid = _alias.resolve_kb(ctx.client, ctx.profile, str(kb_ref), cfg=cfg)
    except (PaisError, LookupError) as e:
        return _manual_fallback(f"could not resolve KB {kb_ref!r} ({e}); type index alias or UUID:")

    idx_alias_by_name: dict[str, str] = {}
    if kb_ref in cfg.knowledge_bases:
        idx_alias_by_name = {ix.name: ix.alias for ix in cfg.knowledge_bases[kb_ref].indexes}

    try:
        indexes = ctx.client.indexes.list(kb_uuid).data
    except PaisError as e:
        return _manual_fallback(f"server unreachable ({e}); type an index alias or UUID:")

    if not indexes:
        return _manual_fallback(f"no indexes under {kb_ref}; type an alias or UUID:")

    choices: list[Any] = []
    value_for_title: dict[str, str] = {}
    for ix in indexes:
        alias = idx_alias_by_name.get(ix.name)
        status = getattr(ix.status, "value", ix.status)
        docs_raw = getattr(ix, "num_documents", None)
        docs = docs_raw if docs_raw is not None else "—"
        if alias:
            title = f"{alias}  —  {ix.name}  (status={status}, docs={docs})"
            value_for_title[title] = alias
        else:
            title = f"—  {ix.name}  (status={status}, docs={docs}, id={ix.id})"
            value_for_title[title] = ix.id
        choices.append(title)
    choices.append(_MANUAL)

    pick = questionary.select(f"Pick an index under {kb_ref}:", choices=choices).ask()
    if pick is None:
        return CANCEL
    if pick == _MANUAL:
        return _manual_fallback("type an index alias or UUID:")
    return value_for_title[pick]


def pick_agent(ctx: PickerContext) -> Any:
    try:
        agents = ctx.client.agents.list().data
    except PaisError as e:
        return _manual_fallback(f"server unreachable ({e}); type an agent UUID:")
    if not agents:
        return _manual_fallback("no agents on the server; type a UUID:")
    choices: list[Any] = []
    value_for_title: dict[str, str] = {}
    for a in agents:
        title = f"{a.name}  —  {getattr(a, 'model', '—')}  ({a.id})"
        value_for_title[title] = a.id
        choices.append(title)
    choices.append(_MANUAL)
    pick = questionary.select("Pick an agent:", choices=choices).ask()
    if pick is None:
        return CANCEL
    if pick == _MANUAL:
        return _manual_fallback("type an agent UUID:")
    return value_for_title[pick]


def pick_splitter_kind(_ctx: PickerContext) -> Any:
    kinds = sorted(SPLITTER_REGISTRY)
    if not kinds:
        return _manual_fallback("no splitters registered; type a kind:")
    pick = questionary.select("Pick a splitter kind:", choices=[*kinds, _MANUAL]).ask()
    if pick is None:
        return CANCEL
    if pick == _MANUAL:
        return _manual_fallback("type a splitter kind:")
    return pick


def pick_cached_alias(_ctx: PickerContext) -> Any:
    cache = _alias.list_cache()
    titles: list[str] = []
    for profile, bucket in cache.items():
        for alias in bucket.get("kbs", {}):
            titles.append(f"{profile}/{alias}")
        for alias in bucket.get("indexes", {}):
            titles.append(f"{profile}/{alias}")
    if not titles:
        return _manual_fallback("alias cache is empty; type an alias to clear (or just Enter):")
    pick = questionary.select("Pick a cached alias to clear:", choices=[*titles, _MANUAL]).ask()
    if pick is None:
        return CANCEL
    if pick == _MANUAL:
        return _manual_fallback("type an alias:")
    return pick.split("/", 1)[1]  # just the alias part — clear() takes alias name


def pick_mcp_tool(ctx: PickerContext) -> Any:
    try:
        tools = ctx.client.mcp_tools.list(server="built-in").data
    except PaisError as e:
        return _manual_fallback(f"could not list MCP tools ({e}); type tool UUID (or skip):")
    if not tools:
        return _manual_fallback("no MCP tools available; type a tool UUID (or skip):")
    choices: list[Any] = []
    value_for_title: dict[str, str] = {}
    for t in tools:
        title = f"{t.name}  —  {getattr(t, 'description', '') or ''}  ({t.id})"
        value_for_title[title] = t.id
        choices.append(title)
    choices.append(_MANUAL)
    pick = questionary.select("Pick an MCP tool to link:", choices=choices).ask()
    if pick is None:
        return CANCEL
    if pick == _MANUAL:
        return _manual_fallback("type a tool UUID (or skip):")
    return value_for_title[pick]


# ----- dispatch table ---------------------------------------------------------


# (command_path, param_name) → picker — most-specific match wins; fallback
# to (None, param_name).
_OVERRIDES: dict[tuple[tuple[str, ...] | None, str], Picker] = {
    (None, "kb_ref"): pick_kb,
    (None, "kb_id"): pick_kb,
    (None, "index_ref"): pick_index,
    (None, "agent_id"): pick_agent,
    (("splitters", "show"), "kind"): pick_splitter_kind,
    (("alias", "clear"), "alias"): pick_cached_alias,
    (("agent", "create"), "kb_search_tool"): pick_mcp_tool,
}


def picker_for(path: tuple[str, ...], param_name: str) -> Picker | None:
    """Return the picker registered for `(path, param_name)`, or None."""
    if (path, param_name) in _OVERRIDES:
        return _OVERRIDES[(path, param_name)]
    if (None, param_name) in _OVERRIDES:
        return _OVERRIDES[(None, param_name)]
    return None


# ----- helpers ----------------------------------------------------------------


def _manual_fallback(prompt: str) -> Any:
    ans = questionary.text(prompt).ask()
    if ans is None:
        return CANCEL
    return ans
