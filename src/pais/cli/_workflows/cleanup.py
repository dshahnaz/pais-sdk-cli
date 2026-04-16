"""Workflow G — Cleanup. Pick a kind → pick an item → type the name to confirm → delete.

Type-to-confirm (GitHub-style) replaces y/N. Honours `PAIS_QUICK_CONFIRM=1`
for power users."""

from __future__ import annotations

import questionary
from rich.console import Console

from pais.cli import _alias, _recent
from pais.cli._config_file import load_profile_config
from pais.cli._pickers import PickerContext, pick_agent, pick_index, pick_kb
from pais.cli._prompts import CANCEL
from pais.cli._workflows._base import Workflow, confirm_by_typing, done_banner
from pais.client import PaisClient
from pais.config import Settings


def run(client: PaisClient, settings: Settings, console: Console) -> None:
    profile = settings.profile or "default"
    cfg, _, _ = load_profile_config()

    kind = questionary.select(
        "Cleanup what?",
        choices=["KB (cascades indexes + docs)", "Index (cascades docs)", "Agent", "Cancel"],
    ).ask()
    if kind is None or kind == "Cancel":
        return

    ctx = PickerContext(client=client, answers={}, profile=profile)

    if kind.startswith("KB"):
        pick = pick_kb(ctx)
        if pick is CANCEL:
            return
        kb_ref = str(pick)
        try:
            kb_uuid = _alias.resolve_kb(client, profile, kb_ref, cfg=cfg)
        except (LookupError, Exception) as e:
            console.print(f"[red]could not resolve: {e}[/red]")
            return
        # Get the resource label for the type-to-confirm prompt
        try:
            kb = client.knowledge_bases.get(kb_uuid)
            label = kb.name
        except Exception:
            label = kb_ref
        if not confirm_by_typing(
            f"This will delete KB '{label}' (uuid={kb_uuid}) and ALL its indexes + documents.",
            expected=label,
        ):
            console.print("[dim]aborted[/dim]")
            return
        client.knowledge_bases.delete(kb_uuid)
        _alias.clear_cache(profile=profile)
        _recent.clear(profile=profile)  # cached recents may now point to a deleted KB
        done_banner(console, "KB deleted", {"name": label, "uuid": kb_uuid})

    elif kind.startswith("Index"):
        pick = pick_kb(ctx)
        if pick is CANCEL:
            return
        kb_ref = str(pick)
        ctx.answers["kb_ref"] = kb_ref
        idx_pick = pick_index(ctx)
        if idx_pick is CANCEL:
            return
        try:
            kb_uuid, idx_uuid = _alias.resolve_index(
                client, profile, kb_ref, str(idx_pick), cfg=cfg
            )
        except (LookupError, Exception) as e:
            console.print(f"[red]could not resolve: {e}[/red]")
            return
        try:
            ix = client.indexes.get(kb_uuid, idx_uuid)
            label = ix.name
        except Exception:
            label = str(idx_pick)
        if not confirm_by_typing(
            f"This will delete index '{label}' (uuid={idx_uuid}) under KB {kb_ref} "
            "and all its documents.",
            expected=label,
        ):
            console.print("[dim]aborted[/dim]")
            return
        client.indexes.delete(kb_uuid, idx_uuid)
        _alias.clear_cache(profile=profile)
        _recent.clear(profile=profile)
        done_banner(console, "Index deleted", {"name": label, "uuid": idx_uuid})

    elif kind == "Agent":
        pick = pick_agent(ctx)
        if pick is CANCEL:
            return
        agent_id = str(pick)
        try:
            agent = client.agents.get(agent_id) if hasattr(client.agents, "get") else None
            label = agent.name if agent else agent_id
        except Exception:
            label = agent_id
        if not confirm_by_typing(
            f"This will delete agent '{label}' (uuid={agent_id}).",
            expected=label,
        ):
            console.print("[dim]aborted[/dim]")
            return
        client.agents.delete(agent_id)
        _recent.clear(profile=profile)
        done_banner(console, "Agent deleted", {"name": label, "uuid": agent_id})


WORKFLOW = Workflow(
    name="Cleanup (delete KB / index / agent)",
    icon="🗑",
    description="Type-to-confirm destructive removal.",
    run=run,
)
