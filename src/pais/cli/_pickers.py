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

from pais.cli import _alias, _recent
from pais.cli._config_file import load_profile_config
from pais.cli._prompts import CANCEL
from pais.client import PaisClient
from pais.errors import PaisError
from pais.ingest.registry import SPLITTER_REGISTRY

_MANUAL = "✏  enter manually"
_CREATE = "+  create new"
_BACK = "←  back"
_DIVIDER = "─" * 30
_BACK_HINT = "Ctrl-C / Esc → back"


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
    choices.append(_DIVIDER)
    choices.append(_MANUAL)
    choices.append(_BACK)

    pick = questionary.select("Pick a KB:", choices=choices, instruction=_BACK_HINT).ask()
    if pick is None or pick in (_BACK, _DIVIDER):
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
    choices.append(_DIVIDER)
    choices.append(_MANUAL)
    choices.append(_BACK)

    pick = questionary.select(
        f"Pick an index under {kb_ref}:", choices=choices, instruction=_BACK_HINT
    ).ask()
    if pick is None or pick in (_BACK, _DIVIDER):
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
    choices.append(_DIVIDER)
    choices.append(_MANUAL)
    choices.append(_BACK)
    pick = questionary.select("Pick an agent:", choices=choices, instruction=_BACK_HINT).ask()
    if pick is None or pick in (_BACK, _DIVIDER):
        return CANCEL
    if pick == _MANUAL:
        return _manual_fallback("type an agent UUID:")
    return value_for_title[pick]


def pick_splitter_kind(_ctx: PickerContext) -> Any:
    kinds = sorted(SPLITTER_REGISTRY)
    if not kinds:
        return _manual_fallback("no splitters registered; type a kind:")
    pick = questionary.select(
        "Pick a splitter kind:",
        choices=[*kinds, _DIVIDER, _MANUAL, _BACK],
        instruction=_BACK_HINT,
    ).ask()
    if pick is None or pick in (_BACK, _DIVIDER):
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
    pick = questionary.select(
        "Pick a cached alias to clear:",
        choices=[*titles, _DIVIDER, _MANUAL, _BACK],
        instruction=_BACK_HINT,
    ).ask()
    if pick is None or pick in (_BACK, _DIVIDER):
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
    choices.append(_DIVIDER)
    choices.append(_MANUAL)
    choices.append(_BACK)
    pick = questionary.select(
        "Pick an MCP tool to link:", choices=choices, instruction=_BACK_HINT
    ).ask()
    if pick is None or pick in (_BACK, _DIVIDER):
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


# ----- pick-or-create variants -----------------------------------------------
#
# Used by workflows. Each prepends ★-marked recents (from `_recent`), lists
# existing items, then offers `+ create new` and `✏ enter manually`. Returns:
#   - the resolved alias / UUID for an existing or recent pick,
#   - the sentinel CREATE_NEW (a literal string) if the user wants to create
#     (the workflow handles the create flow inline),
#   - CANCEL on Esc.

CREATE_NEW = "__create_new__"


def pick_or_create_kb(ctx: PickerContext) -> Any:
    """Like `pick_kb` but with recents at top + a `+ create new` option."""
    cfg, _, _ = load_profile_config()
    name_to_alias = {decl.name: alias for alias, decl in cfg.knowledge_bases.items()}
    try:
        kbs = ctx.client.knowledge_bases.list().data
    except PaisError as e:
        return _manual_fallback(f"server unreachable ({e}); type a KB alias or UUID:")

    recents = _recent.recent("kbs", profile=ctx.profile)
    choices: list[Any] = []
    value_for_title: dict[str, str] = {}

    # Build {alias_or_uuid: title} for existing KBs.
    pairs: list[tuple[str, str]] = []
    for kb in kbs:
        alias = name_to_alias.get(kb.name)
        title = f"{alias}  —  {kb.name}  ({kb.id})" if alias else f"—  {kb.name}  ({kb.id})"
        pairs.append((alias or kb.id, title))
    title_for_value = {v: t for v, t in pairs}

    # 1) recents (only those still on the server)
    for r in recents:
        if r in title_for_value:
            t = f"★  {title_for_value[r]}"
            choices.append(t)
            value_for_title[t] = r

    if recents and any(c.startswith("★") for c in choices):
        choices.append(_DIVIDER)

    # 2) all existing KBs (not duplicating recents)
    seen = {value_for_title[c] for c in choices if c != _DIVIDER}
    for v, t in pairs:
        if v in seen:
            continue
        choices.append(t)
        value_for_title[t] = v

    # 3) actions
    if choices and choices[-1] != _DIVIDER:
        choices.append(_DIVIDER)
    choices.append(_CREATE)
    choices.append(_MANUAL)
    choices.append(_BACK)

    pick = questionary.select(
        "Pick a KB (or create new):", choices=choices, instruction=_BACK_HINT
    ).ask()
    if pick is None or pick in (_BACK, _DIVIDER):
        return CANCEL
    if pick == _CREATE:
        return CREATE_NEW
    if pick == _MANUAL:
        return _manual_fallback("type a KB alias or UUID:")
    return value_for_title[pick]


def pick_or_create_index(ctx: PickerContext) -> Any:
    """Like `pick_index` but with recents + `+ create new`. Requires
    `ctx.answers['kb_ref']` (or 'kb_id') to scope the index list."""
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

    recents = _recent.recent("indexes", profile=ctx.profile)
    pairs: list[tuple[str, str]] = []
    for ix in indexes:
        alias = idx_alias_by_name.get(ix.name)
        status = getattr(ix.status, "value", ix.status)
        docs_raw = getattr(ix, "num_documents", None)
        docs = docs_raw if docs_raw is not None else "—"
        if alias:
            full_alias = f"{kb_ref}:{alias}"
            title = f"{full_alias}  —  {ix.name}  (status={status}, docs={docs})"
            pairs.append((full_alias, title))
        else:
            title = f"—  {ix.name}  (status={status}, docs={docs}, id={ix.id})"
            pairs.append((ix.id, title))
    title_for_value = {v: t for v, t in pairs}

    choices: list[Any] = []
    value_for_title: dict[str, str] = {}
    for r in recents:
        if r in title_for_value:
            t = f"★  {title_for_value[r]}"
            choices.append(t)
            value_for_title[t] = r
    if recents and any(c.startswith("★") for c in choices):
        choices.append(_DIVIDER)
    seen = {value_for_title[c] for c in choices if c != _DIVIDER}
    for v, t in pairs:
        if v in seen:
            continue
        choices.append(t)
        value_for_title[t] = v
    if choices and choices[-1] != _DIVIDER:
        choices.append(_DIVIDER)
    choices.append(_CREATE)
    choices.append(_MANUAL)
    choices.append(_BACK)

    pick = questionary.select(
        f"Pick an index under {kb_ref} (or create new):",
        choices=choices,
        instruction=_BACK_HINT,
    ).ask()
    if pick is None or pick in (_BACK, _DIVIDER):
        return CANCEL
    if pick == _CREATE:
        return CREATE_NEW
    if pick == _MANUAL:
        return _manual_fallback("type an index alias or UUID:")
    return value_for_title[pick]


def pick_or_create_agent(ctx: PickerContext) -> Any:
    """Pick from existing agents or create a new one. Returns agent UUID or CREATE_NEW."""
    try:
        agents = ctx.client.agents.list().data
    except PaisError as e:
        return _manual_fallback(f"server unreachable ({e}); type an agent UUID:")

    recents = _recent.recent("agents", profile=ctx.profile)
    pairs: list[tuple[str, str]] = []
    for a in agents:
        title = f"{a.name}  —  {getattr(a, 'model', '—')}  ({a.id})"
        pairs.append((a.id, title))
    title_for_value = {v: t for v, t in pairs}

    choices: list[Any] = []
    value_for_title: dict[str, str] = {}
    for r in recents:
        if r in title_for_value:
            t = f"★  {title_for_value[r]}"
            choices.append(t)
            value_for_title[t] = r
    if recents and any(c.startswith("★") for c in choices):
        choices.append(_DIVIDER)
    seen = {value_for_title[c] for c in choices if c != _DIVIDER}
    for v, t in pairs:
        if v in seen:
            continue
        choices.append(t)
        value_for_title[t] = v
    if choices and choices[-1] != _DIVIDER:
        choices.append(_DIVIDER)
    choices.append(_CREATE)
    choices.append(_MANUAL)
    choices.append(_BACK)

    pick = questionary.select(
        "Pick an agent (or create new):", choices=choices, instruction=_BACK_HINT
    ).ask()
    if pick is None or pick in (_BACK, _DIVIDER):
        return CANCEL
    if pick == _CREATE:
        return CREATE_NEW
    if pick == _MANUAL:
        return _manual_fallback("type an agent UUID:")
    return value_for_title[pick]


def pick_or_create_splitter_config(_ctx: PickerContext) -> Any:
    """Pick a registered splitter kind. Each row shows summary + typical chunk size."""
    from pais.ingest.splitters._base import meta_for

    kinds = sorted(SPLITTER_REGISTRY)
    if not kinds:
        return _manual_fallback("no splitters registered; type a kind:")

    titles: list[str] = []
    value_for_title: dict[str, str] = {}
    for k in kinds:
        m = meta_for(SPLITTER_REGISTRY[k])
        title = f"{k:20s} — {m.summary}  [{m.typical_chunk_size}]"
        titles.append(title)
        value_for_title[title] = k

    pick = questionary.select(
        "Pick a splitter kind:",
        choices=[*titles, _DIVIDER, _MANUAL, _BACK],
        instruction=_BACK_HINT,
    ).ask()
    if pick is None or pick in (_BACK, _DIVIDER):
        return CANCEL
    if pick == _MANUAL:
        return _manual_fallback("type a splitter kind:")
    return value_for_title[pick]


# ----- helpers ----------------------------------------------------------------


def _manual_fallback(prompt: str) -> Any:
    ans = questionary.text(prompt).ask()
    if ans is None:
        return CANCEL
    return ans
