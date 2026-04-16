"""Workflow B — Ingest data into an index.

Pick KB+index → splitter from config-or-prompt → path → optional --replace →
run with progress bar → branch to search to verify."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import questionary
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from pais.cli import _alias
from pais.cli._config_file import load_profile_config
from pais.cli._pickers import (
    PickerContext,
    pick_index,
    pick_kb,
    pick_or_create_splitter_config,
)
from pais.cli._prompts import CANCEL
from pais.cli._workflows._base import (
    NextAction,
    Workflow,
    branch_yes_no,
    done_banner,
    next_actions_menu,
)
from pais.client import PaisClient
from pais.config import Settings
from pais.ingest.registry import get_splitter
from pais.ingest.runner import ingest_path


def run(
    client: PaisClient,
    settings: Settings,
    console: Console,
    *,
    _preset: dict[str, Any] | None = None,
) -> None:
    profile = settings.profile or "default"
    cfg, _, _ = load_profile_config()

    if _preset:
        kb_uuid = _preset["kb_uuid"]
        idx_uuid = _preset["idx_uuid"]
        kb_ref_for_lookup: str | None = None
    else:
        ctx = PickerContext(client=client, answers={}, profile=profile)
        kb_pick = pick_kb(ctx)
        if kb_pick is CANCEL:
            return
        kb_ref_for_lookup = str(kb_pick)
        ctx.answers["kb_ref"] = kb_ref_for_lookup
        idx_pick = pick_index(ctx)
        if idx_pick is CANCEL:
            return
        try:
            kb_uuid, idx_uuid = _alias.resolve_index(
                client, profile, kb_ref_for_lookup, str(idx_pick), cfg=cfg
            )
        except (LookupError, Exception) as e:
            console.print(f"[red]could not resolve {kb_ref_for_lookup}:{idx_pick} — {e}[/red]")
            return

    # Splitter — pull from config first, fall back to prompt.
    splitter_kind: str | None = None
    splitter_options: Any = None
    if kb_ref_for_lookup and kb_ref_for_lookup in cfg.knowledge_bases:
        # Find the index decl with matching uuid (best-effort)
        for ix_decl in cfg.knowledge_bases[kb_ref_for_lookup].indexes:
            if ix_decl.splitter is not None:
                splitter_kind = ix_decl.splitter.kind
                splitter_options = ix_decl.splitter.options()
                console.print(f"[dim]using config: splitter={splitter_kind}[/dim]")
                break

    if splitter_kind is None:
        ctx2 = PickerContext(client=client, answers={}, profile=profile)
        kind_pick = pick_or_create_splitter_config(ctx2)
        if kind_pick is CANCEL:
            return
        splitter_kind = str(kind_pick)
        cls = get_splitter(splitter_kind)
        splitter_options = cls.options_model()  # all defaults

    cls = get_splitter(splitter_kind)
    splitter = cls(splitter_options)

    # Splitter brief — show the user exactly what they're about to run.
    from pais.ingest.splitters._base import meta_for

    m = meta_for(cls)
    hint = f"  [dim]({m.token_char_hint})[/dim]" if m.token_char_hint else ""
    console.print(
        f"\n[bold]splitter:[/bold] [cyan]{splitter_kind}[/cyan]\n"
        f"  [dim]input:[/dim] {m.input_type}\n"
        f"  [dim]chunk:[/dim] {m.typical_chunk_size}{hint}\n"
    )

    # Path
    path_str = questionary.path("path (file or directory):").ask()
    if not path_str:
        return
    path = Path(path_str).expanduser()
    if not path.exists():
        console.print(f"[red]path not found: {path}[/red]")
        return

    replace = branch_yes_no("Replace existing docs whose origin_name matches?", default=False)

    # Run with progress
    files = list(path.rglob("*")) if path.is_dir() else [path]
    total = sum(1 for f in files if f.is_file())
    with Progress(
        TextColumn("[bold]ingest[/bold]"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("ingesting", total=total)

        def _on_file(_p: str) -> None:
            progress.advance(task)

        report = ingest_path(
            client,
            path,
            splitter=splitter,
            kb_id=kb_uuid,
            index_id=idx_uuid,
            workers=4,
            replace=replace,
            dry_run=False,
            progress=_on_file,
        )

    done_banner(
        console,
        "Ingest complete",
        {
            "files": report.total_files,
            "failed": report.total_failed,
            "chunks_uploaded": report.total_chunks_uploaded,
            "deleted": report.total_existing_deleted,
        },
    )

    next_actions_menu(
        [
            NextAction(
                label="🔎  Test with a search",
                callback=lambda: _branch_to_search(client, settings, console, kb_uuid, idx_uuid),
                recommended=True,
            ),
            NextAction(label="✅  Done", callback=None),
        ],
        console,
    )


def _branch_to_search(
    client: PaisClient,
    settings: Settings,
    console: Console,
    kb_uuid: str,
    idx_uuid: str,
) -> None:
    from pais.cli._workflows.search import run as run_search

    run_search(client, settings, console, _preset={"kb_uuid": kb_uuid, "idx_uuid": idx_uuid})


WORKFLOW = Workflow(
    name="Ingest data into an index",
    icon="📥",
    description="Run a splitter over a file/dir and upload chunks.",
    run=run,
)
