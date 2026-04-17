"""Workflow A — Set up a chat agent over my docs.

The user's primary use case. End-to-end: pick-or-create KB → pick-or-create
index → optional save to TOML → create agent (doc-aligned: index_id directly,
no MCP indirection) → branch into ingest / chat.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import questionary
from rich.console import Console

from pais.cli import _alias, _recent
from pais.cli._config_file import load_profile_config
from pais.cli._config_writeback import (
    WritebackError,
    append_index_block,
    append_kb_block,
    block_exists,
    commit_append,
    index_exists,
    preview_diff,
)
from pais.cli._pickers import (
    CREATE_NEW,
    PickerContext,
    first_model_id,
    pick_chat_model,
    pick_embeddings_model,
    pick_or_create_index,
    pick_or_create_kb,
)
from pais.cli._prompts import CANCEL
from pais.cli._workflows._base import (
    BACK,
    FieldSpec,
    NextAction,
    ReviewSpec,
    Workflow,
    branch_yes_no,
    done_banner,
    next_actions_menu,
    prompt_review_screen,
)
from pais.client import PaisClient
from pais.config import Settings
from pais.models import (
    AgentCreate,
    DataOriginType,
    IndexCreate,
    KnowledgeBaseCreate,
    TextSplittingKind,
)


def _stamp() -> str:
    return _dt.datetime.utcnow().strftime("%Y_%m_%d")


def run(client: PaisClient, settings: Settings, console: Console) -> None:
    profile = settings.profile or "default"
    cfg, cfg_path, _ = load_profile_config()
    if cfg_path is None:
        cfg_path = Path.cwd() / "pais.toml"

    if settings.mode == "mock":
        console.print(
            "[yellow][mock][/yellow] this run hits the in-process mock; "
            "no real PAIS calls — safe to experiment.\n"
        )
    console.print(
        "[bold]Set up a chat agent over my docs.[/bold] "
        "[dim]I'll walk you through KB → index → agent (3 server calls). "
        "Esc cancels anytime.[/dim]\n"
    )

    # ---- KB step --------------------------------------------------------
    ctx = PickerContext(client=client, answers={}, profile=profile)
    kb_pick = pick_or_create_kb(ctx)
    if kb_pick is CANCEL:
        console.print("[dim]aborted; back to menu[/dim]")
        return

    kb_alias_for_toml: str | None = None
    kb_uuid: str
    kb_name_for_summary: str

    if kb_pick == CREATE_NEW:
        spec = ReviewSpec(
            title="Create KB",
            fields=[
                FieldSpec(name="name", value=f"kb_{_stamp()}"),
                FieldSpec(
                    name="description",
                    value=None,
                    re_prompt=lambda _v: questionary.text("description (optional):").ask(),
                ),
                FieldSpec(
                    name="data_origin_type",
                    value=DataOriginType.DATA_SOURCES.value,
                    hint="DATA_SOURCES (doc-aligned), or LOCAL_FILES",
                    re_prompt=lambda _v: questionary.select(
                        "data_origin_type:",
                        choices=["DATA_SOURCES", "LOCAL_FILES", "DATA_SOURCE"],
                    ).ask(),
                ),
            ],
        )
        result = prompt_review_screen(spec, console)
        if result is CANCEL or result is BACK:
            console.print("[dim]aborted; back to menu[/dim]")
            return
        assert isinstance(result, dict)
        kb = client.knowledge_bases.create(
            KnowledgeBaseCreate(
                name=result["name"],
                description=result["description"],
                data_origin_type=DataOriginType(result["data_origin_type"]),
            )
        )
        kb_uuid = kb.id
        kb_name_for_summary = kb.name
        _alias.clear_cache(profile=profile)  # invalidate so resolver re-fetches
        done_banner(
            console,
            "KB created",
            {"name": kb.name, "uuid": kb.id, "data_origin_type": result["data_origin_type"]},
        )

        # Offer to save to TOML
        suggested_alias = _suggest_alias(kb.name)
        if branch_yes_no(f"Save as alias '{suggested_alias}' in {cfg_path}?", default=True):
            kb_alias_for_toml = _maybe_edit_alias(suggested_alias)
            try:
                if not block_exists(cfg_path, profile, kb_alias_for_toml):
                    block = append_kb_block(
                        config_path=cfg_path,
                        profile=profile,
                        alias=kb_alias_for_toml,
                        kb_name=kb.name,
                        description=result["description"],
                        data_origin_type=result["data_origin_type"],
                    )
                    diff = preview_diff(cfg_path, block)
                    console.print("[dim]preview:[/dim]")
                    console.print(diff or "[dim](no diff — file unchanged)[/dim]")
                    if branch_yes_no("Write?", default=True):
                        commit_append(cfg_path, block)
                        console.print(f"[green]✓ wrote {cfg_path}[/green]")
                else:
                    console.print(
                        f"[dim]alias '{kb_alias_for_toml}' already exists in {cfg_path} — skipped[/dim]"
                    )
            except WritebackError as e:
                console.print(f"[red]TOML writeback skipped:[/red] {e}")
                kb_alias_for_toml = None
        _recent.record_use("kbs", kb_alias_for_toml or kb.id, profile=profile)
    else:
        # Existing KB picked
        kb_ref_str = str(kb_pick)
        try:
            kb_uuid = _alias.resolve_kb(client, profile, kb_ref_str, cfg=cfg)
        except (LookupError, Exception) as e:
            console.print(f"[red]could not resolve KB {kb_ref_str!r}: {e}[/red]")
            return
        kb_name_for_summary = kb_ref_str
        if kb_ref_str in cfg.knowledge_bases:
            kb_alias_for_toml = kb_ref_str
        _recent.record_use("kbs", kb_ref_str, profile=profile)

    # ---- Index step -----------------------------------------------------
    cfg, _cfg_path2, _ = load_profile_config()  # re-read in case TOML changed
    ctx.answers["kb_ref"] = kb_alias_for_toml or kb_uuid
    idx_pick = pick_or_create_index(ctx)
    if idx_pick is CANCEL:
        console.print("[dim]aborted; back to menu[/dim]")
        return

    idx_uuid: str
    idx_alias_for_toml: str | None = None
    idx_name_for_summary: str

    if idx_pick == CREATE_NEW:
        spec = ReviewSpec(
            title=f"Create index under {kb_name_for_summary}",
            fields=[
                FieldSpec(name="name", value=f"ix_{_stamp()}"),
                FieldSpec(
                    name="embeddings_model_endpoint",
                    value=first_model_id(ctx, kind="EMBEDDINGS") or "BAAI/bge-small-en-v1.5",
                    hint="pick from server-advertised embeddings models",
                    re_prompt=lambda _v: pick_embeddings_model(ctx),
                ),
                FieldSpec(
                    name="text_splitting",
                    value="SENTENCE",
                    hint="SENTENCE (default)",
                    re_prompt=lambda _v: questionary.select(
                        "text_splitting:", choices=["SENTENCE"]
                    ).ask(),
                ),
                FieldSpec(
                    name="chunk_size",
                    value=512,
                    hint="≈ 2KB English text per chunk; tokens, not chars",
                    re_prompt=lambda _v: _int_prompt("chunk_size:", default=512),
                ),
                FieldSpec(
                    name="chunk_overlap",
                    value=64,
                    hint="overlap between adjacent chunks (tokens)",
                    re_prompt=lambda _v: _int_prompt("chunk_overlap:", default=64),
                ),
            ],
        )
        result = prompt_review_screen(spec, console)
        if result is CANCEL or result is BACK:
            console.print("[dim]aborted; back to menu[/dim]")
            return
        assert isinstance(result, dict)
        ix = client.indexes.create(
            kb_uuid,
            IndexCreate(
                name=result["name"],
                embeddings_model_endpoint=result["embeddings_model_endpoint"],
                text_splitting=TextSplittingKind(result["text_splitting"]),
                chunk_size=int(result["chunk_size"]),
                chunk_overlap=int(result["chunk_overlap"]),
            ),
        )
        idx_uuid = ix.id
        idx_name_for_summary = ix.name
        _alias.clear_cache(profile=profile)
        done_banner(console, "Index created", {"name": ix.name, "uuid": ix.id})

        # Save index to TOML alongside the KB if KB has an alias
        if kb_alias_for_toml and branch_yes_no(
            f"Save index as alias under '{kb_alias_for_toml}' in {cfg_path}?",
            default=True,
        ):
            suggested = _suggest_alias(ix.name)
            idx_alias_for_toml = _maybe_edit_alias(suggested)
            try:
                if not index_exists(cfg_path, profile, kb_alias_for_toml, idx_alias_for_toml):
                    block = append_index_block(
                        profile=profile,
                        kb_alias=kb_alias_for_toml,
                        idx_alias=idx_alias_for_toml,
                        name=ix.name,
                        embeddings_model_endpoint=result["embeddings_model_endpoint"],
                        text_splitting=result["text_splitting"],
                        chunk_size=int(result["chunk_size"]),
                        chunk_overlap=int(result["chunk_overlap"]),
                    )
                    diff = preview_diff(cfg_path, block)
                    console.print("[dim]preview:[/dim]")
                    console.print(diff or "[dim](no diff)[/dim]")
                    if branch_yes_no("Write?", default=True):
                        commit_append(cfg_path, block)
                        console.print(f"[green]✓ wrote {cfg_path}[/green]")
                else:
                    console.print("[dim]index alias already declared — skipped[/dim]")
            except WritebackError as e:
                console.print(f"[red]TOML writeback skipped:[/red] {e}")
                idx_alias_for_toml = None
        _recent.record_use(
            "indexes",
            f"{kb_alias_for_toml}:{idx_alias_for_toml}"
            if (kb_alias_for_toml and idx_alias_for_toml)
            else ix.id,
            profile=profile,
        )
    else:
        idx_ref_str = str(idx_pick)
        try:
            _kb_uuid_check, idx_uuid = _alias.resolve_index(
                client,
                profile,
                kb_alias_for_toml or kb_uuid,
                idx_ref_str.split(":")[-1] if ":" in idx_ref_str else idx_ref_str,
                cfg=cfg,
            )
        except (LookupError, Exception) as e:
            console.print(f"[red]could not resolve index {idx_ref_str!r}: {e}[/red]")
            return
        idx_name_for_summary = idx_ref_str
        _recent.record_use("indexes", idx_ref_str, profile=profile)

    # ---- Agent step -----------------------------------------------------
    spec = ReviewSpec(
        title="Create agent",
        fields=[
            FieldSpec(name="name", value=f"agent_{_stamp()}"),
            FieldSpec(
                name="model",
                value=first_model_id(ctx, kind="COMPLETIONS") or "openai/gpt-oss-120b-4x",
                hint="pick from server-advertised chat models",
                re_prompt=lambda _v: pick_chat_model(ctx),
            ),
            FieldSpec(
                name="instructions",
                value=None,
                hint="(optional) system prompt — opens text editor on Edit",
                re_prompt=lambda _v: questionary.text(
                    "instructions (multi-line; ⏎⏎ to finish):", multiline=True
                ).ask(),
            ),
            FieldSpec(
                name="index_id",
                value=idx_uuid,
                hint=f"auto-filled from {idx_name_for_summary}",
                editable=False,
            ),
            FieldSpec(
                name="index_top_n",
                value=5,
                hint="number of search hits to include per turn",
                re_prompt=lambda _v: _int_prompt("index_top_n:", default=5),
            ),
            FieldSpec(
                name="index_similarity_cutoff",
                value=0.0,
                hint="minimum similarity score (0.0 disables filtering)",
                re_prompt=lambda _v: _float_prompt("index_similarity_cutoff:", default=0.0),
            ),
        ],
    )
    result = prompt_review_screen(spec, console)
    if result is CANCEL or result is BACK:
        console.print("[dim]aborted; back to menu[/dim]")
        return
    assert isinstance(result, dict)

    agent = client.agents.create(
        AgentCreate(
            name=result["name"],
            model=result["model"],
            instructions=result["instructions"],
            index_id=result["index_id"],
            index_top_n=int(result["index_top_n"]),
            index_similarity_cutoff=float(result["index_similarity_cutoff"]),
        )
    )
    _recent.record_use("agents", agent.id, profile=profile)
    done_banner(
        console,
        f'Agent "{agent.name}" created',
        {
            "uuid": agent.id,
            "model": agent.model,
            "index_id": agent.index_id,
            "index_top_n": agent.index_top_n,
        },
    )

    # ---- Post-success "what next?" -------------------------------------
    next_actions_menu(
        [
            NextAction(
                label="📥  Ingest data into this index now",
                callback=lambda: _branch_to_ingest(client, settings, console, kb_uuid, idx_uuid),
                annotation="recommended — index is empty",
                recommended=True,
            ),
            NextAction(
                label="💬  Chat with this agent",
                callback=lambda: _branch_to_chat(client, settings, console, agent.id),
                annotation="(mock — canned answers)" if settings.mode == "mock" else None,
            ),
            NextAction(
                label="📊  View `pais status`",
                callback=lambda: _branch_to_status(client, settings, console),
            ),
            NextAction(
                label=f"📋  Copy as `pais agent chat {agent.id} '<question>'`",
                callback=None,
            ),
            NextAction(label="✅  Done", callback=None),
        ],
        console,
    )


# ----- helpers --------------------------------------------------------------


def _suggest_alias(name: str) -> str:
    """Convert a human name to a TOML-safe alias."""
    import re

    s = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_")
    if not s or not s[0].isalpha():
        s = "x_" + s
    return s


def _maybe_edit_alias(suggested: str) -> str:
    ans = questionary.text("alias:", default=suggested).ask()
    return ans or suggested


def _int_prompt(label: str, *, default: int) -> int:
    ans = questionary.text(
        label,
        default=str(default),
        validate=lambda v: v.strip().isdigit() or "must be a positive integer",
    ).ask()
    return int(ans) if ans else default


def _float_prompt(label: str, *, default: float) -> float:
    def _ok(v: str) -> bool | str:
        try:
            float(v)
            return True
        except ValueError:
            return "must be a number"

    ans = questionary.text(label, default=str(default), validate=_ok).ask()
    return float(ans) if ans else default


def _branch_to_ingest(
    client: PaisClient,
    settings: Settings,
    console: Console,
    kb_uuid: str,
    idx_uuid: str,
) -> None:
    from pais.cli._workflows.ingest_data import run as run_ingest

    # Pre-seed answers so the user doesn't re-pick KB+index.
    run_ingest(client, settings, console, _preset={"kb_uuid": kb_uuid, "idx_uuid": idx_uuid})


def _branch_to_chat(
    client: PaisClient, settings: Settings, console: Console, agent_id: str
) -> None:
    from pais.cli._workflows.chat import run as run_chat

    run_chat(client, settings, console, _preset={"agent_id": agent_id})


def _branch_to_status(client: PaisClient, settings: Settings, console: Console) -> None:
    from pais.cli.status_cmd import status as status_cmd

    status_cmd(with_counts=True, epoch=False, no_ping=settings.mode == "mock", output="table")


WORKFLOW = Workflow(
    name="Set up a chat agent over my docs",
    icon="🤖",
    description="End-to-end: KB → index → agent (no UUID typing).",
    run=run,
)
