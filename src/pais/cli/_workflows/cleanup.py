"""Workflow G — Cleanup. Pick a kind → pick an item → type the name to confirm → delete.

v0.6.4: visible-red banner when delete fails (typo, server error, or undocumented
endpoint), verify-after-delete by re-fetching, and an alternatives menu when
the deployment doesn't expose per-index DELETE.
"""

from __future__ import annotations

import questionary
from rich.console import Console

from pais.cli import _alias, _recent
from pais.cli._config_file import load_profile_config
from pais.cli._pickers import PickerContext, pick_agent, pick_index, pick_kb
from pais.cli._prompts import CANCEL
from pais.cli._workflows._base import (
    Workflow,
    confirm_by_typing,
    done_banner,
    error_banner,
    partial_banner,
)
from pais.client import PaisClient
from pais.config import Settings
from pais.errors import IndexDeleteUnsupported, PaisError, PaisNotFoundError


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
        _delete_kb(client, console, ctx, profile, cfg)
    elif kind.startswith("Index"):
        _delete_index(client, console, ctx, profile, cfg)
    elif kind == "Agent":
        _delete_agent(client, console, ctx, profile)


# ----- KB --------------------------------------------------------------------


def _delete_kb(client, console, ctx, profile, cfg) -> None:  # type: ignore[no-untyped-def]
    pick = pick_kb(ctx)
    if pick is CANCEL:
        return
    kb_ref = str(pick)
    try:
        kb_uuid = _alias.resolve_kb(client, profile, kb_ref, cfg=cfg)
    except (LookupError, PaisError) as e:
        console.print(f"[red]could not resolve: {e}[/red]")
        return
    try:
        kb = client.knowledge_bases.get(kb_uuid)
        label = kb.name
    except Exception:
        label = kb_ref
    if not _typed_confirm_or_warn(
        console,
        prompt=f"This will delete KB '{label}' (uuid={kb_uuid}) and ALL its indexes + documents.",
        expected=label,
    ):
        return
    try:
        client.knowledge_bases.delete(kb_uuid)
    except PaisError as e:
        error_banner(console, "KB delete failed", {"name": label, "uuid": kb_uuid, "error": str(e)})
        return
    _alias.clear_cache(profile=profile)
    _recent.clear(profile=profile)
    _verify_gone(
        console,
        title=f"KB '{label}' deleted",
        summary={"name": label, "uuid": kb_uuid},
        get_fn=lambda: client.knowledge_bases.get(kb_uuid),
    )


# ----- Index -----------------------------------------------------------------


def _delete_index(client, console, ctx, profile, cfg) -> None:  # type: ignore[no-untyped-def]
    pick = pick_kb(ctx)
    if pick is CANCEL:
        return
    kb_ref = str(pick)
    ctx.answers["kb_ref"] = kb_ref
    idx_pick = pick_index(ctx)
    if idx_pick is CANCEL:
        return
    try:
        kb_uuid, idx_uuid = _alias.resolve_index(client, profile, kb_ref, str(idx_pick), cfg=cfg)
    except (LookupError, PaisError) as e:
        console.print(f"[red]could not resolve: {e}[/red]")
        return
    try:
        ix = client.indexes.get(kb_uuid, idx_uuid)
        label = ix.name
    except Exception:
        label = str(idx_pick)
    if not _typed_confirm_or_warn(
        console,
        prompt=(
            f"This will delete index '{label}' (uuid={idx_uuid}) under KB {kb_ref} "
            "and all its documents."
        ),
        expected=label,
    ):
        return
    try:
        client.indexes.delete(kb_uuid, idx_uuid)
    except IndexDeleteUnsupported as e:
        # Surface the actionable alternatives instead of pretending it worked.
        error_banner(
            console,
            "Index DELETE not supported by this PAIS deployment",
            {"index": label, "uuid": idx_uuid, "kb_ref": kb_ref, "detail": str(e)},
        )
        _offer_index_alternatives(client, console, kb_ref, kb_uuid, idx_uuid, label)
        return
    except PaisError as e:
        error_banner(
            console,
            "Index delete failed",
            {"index": label, "uuid": idx_uuid, "error": str(e)},
        )
        return
    _alias.clear_cache(profile=profile)
    _recent.clear(profile=profile)
    _verify_gone(
        console,
        title=f"Index '{label}' deleted",
        summary={"index": label, "uuid": idx_uuid, "kb_ref": kb_ref},
        get_fn=lambda: client.indexes.get(kb_uuid, idx_uuid),
    )


def _offer_index_alternatives(  # type: ignore[no-untyped-def]
    client, console, kb_ref, kb_uuid, idx_uuid, idx_label
) -> None:
    """Index delete is unsupported. Walk the user through the real options."""
    choice = questionary.select(
        "Index DELETE is not available on this deployment. What now?",
        choices=[
            "Delete the parent KB (cascades all indexes + documents)",
            "Purge contents (--strategy recreate; changes the index_id)",
            "← back",
        ],
    ).ask()
    if choice is None or choice.startswith("←"):
        return
    if choice.startswith("Delete the parent KB"):
        try:
            kb = client.knowledge_bases.get(kb_uuid)
        except Exception:
            console.print("[red]could not fetch KB to confirm parent name[/red]")
            return
        if not _typed_confirm_or_warn(
            console,
            prompt=(
                f"⚠ This will delete the WHOLE KB '{kb.name}' (uuid={kb_uuid}) — "
                f"every index and every document inside, not just '{idx_label}'."
            ),
            expected=kb.name,
        ):
            return
        try:
            client.knowledge_bases.delete(kb_uuid)
        except PaisError as e:
            error_banner(console, "KB delete failed", {"name": kb.name, "error": str(e)})
            return
        _verify_gone(
            console,
            title=f"KB '{kb.name}' deleted",
            summary={"name": kb.name, "uuid": kb_uuid},
            get_fn=lambda: client.knowledge_bases.get(kb_uuid),
        )
    elif choice.startswith("Purge"):
        try:
            res = client.indexes.purge(kb_uuid, idx_uuid, strategy="recreate")
        except IndexDeleteUnsupported as e:
            error_banner(
                console,
                "Purge --strategy recreate also unavailable",
                {"detail": str(e)},
            )
            return
        except PaisError as e:
            error_banner(console, "Purge failed", {"error": str(e)})
            return
        done_banner(
            console,
            f"Index '{idx_label}' contents purged via recreate",
            {
                "documents_deleted": res.documents_deleted,
                "new_index_id": res.new_index_id or "(unchanged)",
            },
        )


# ----- Agent -----------------------------------------------------------------


def _delete_agent(client, console, ctx, profile) -> None:  # type: ignore[no-untyped-def]
    pick = pick_agent(ctx)
    if pick is CANCEL:
        return
    agent_id = str(pick)
    try:
        agent = client.agents.get(agent_id) if hasattr(client.agents, "get") else None
        label = agent.name if agent else agent_id
    except Exception:
        label = agent_id
    if not _typed_confirm_or_warn(
        console,
        prompt=f"This will delete agent '{label}' (uuid={agent_id}).",
        expected=label,
    ):
        return
    try:
        client.agents.delete(agent_id)
    except PaisError as e:
        error_banner(
            console, "Agent delete failed", {"name": label, "uuid": agent_id, "error": str(e)}
        )
        return
    _recent.clear(profile=profile)
    _verify_gone(
        console,
        title=f"Agent '{label}' deleted",
        summary={"name": label, "uuid": agent_id},
        get_fn=lambda: client.agents.get(agent_id) if hasattr(client.agents, "get") else None,
    )


# ----- helpers ---------------------------------------------------------------


def _typed_confirm_or_warn(console: Console, *, prompt: str, expected: str) -> bool:
    """Wrapper around `confirm_by_typing` that prints a VISIBLE red line on
    mismatch (instead of the dim-grey 'aborted' that's easy to miss)."""
    if confirm_by_typing(prompt, expected=expected):
        return True
    console.print(
        f"[red]✗ name didn't match[/red] [dim](expected '{expected}')[/dim] "
        "[red]— nothing was deleted[/red]"
    )
    return False


def _verify_gone(  # type: ignore[no-untyped-def]
    console, *, title: str, summary: dict[str, object], get_fn
) -> None:
    """Re-fetch the resource. If `PaisNotFoundError` → green ✓; else red ✗.

    A green banner from this workflow now MEANS the resource is verifiably gone
    (not just "the DELETE call returned 200"). Catches:
    - undocumented-endpoint silent no-ops
    - eventual-consistency where the server queued the delete but didn't apply it
    - a server bug that returns 200 without removing the row.
    """
    try:
        still_there = get_fn()
        if still_there is None:
            done_banner(console, title, summary)
            return
        error_banner(
            console,
            f"{title} — but server still lists it",
            {**summary, "hint": "rerun with `pais -v` to see the server response"},
        )
    except PaisNotFoundError:
        done_banner(console, title, summary)
    except PaisError as e:
        partial_banner(
            console,
            f"{title} — couldn't verify",
            {**summary, "verify_error": str(e)},
        )


WORKFLOW = Workflow(
    name="Cleanup (delete KB / index / agent)",
    icon="🗑",
    description="Type-to-confirm + verifies deletion + offers alternatives when unsupported.",
    run=run,
)
