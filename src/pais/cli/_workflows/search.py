"""Workflow F — Search an index (no LLM, raw hits)."""

from __future__ import annotations

from typing import Any

import questionary
from rich.console import Console
from rich.table import Table

from pais.cli import _alias
from pais.cli._config_file import load_profile_config
from pais.cli._pickers import PickerContext, pick_index, pick_kb
from pais.cli._prompts import CANCEL
from pais.cli._workflows._base import Workflow, branch_yes_no
from pais.client import PaisClient
from pais.config import Settings
from pais.models import SearchQuery


def run(
    client: PaisClient,
    settings: Settings,
    console: Console,
    *,
    _preset: dict[str, Any] | None = None,
) -> None:
    profile = settings.profile or "default"
    if _preset:
        kb_uuid = _preset["kb_uuid"]
        idx_uuid = _preset["idx_uuid"]
    else:
        cfg, _, _ = load_profile_config()
        ctx = PickerContext(client=client, answers={}, profile=profile)
        kb_pick = pick_kb(ctx)
        if kb_pick is CANCEL:
            return
        ctx.answers["kb_ref"] = str(kb_pick)
        idx_pick = pick_index(ctx)
        if idx_pick is CANCEL:
            return
        try:
            kb_uuid, idx_uuid = _alias.resolve_index(
                client, profile, str(kb_pick), str(idx_pick), cfg=cfg
            )
        except (LookupError, Exception) as e:
            console.print(f"[red]could not resolve: {e}[/red]")
            return

    query = questionary.text("query:").ask()
    if not query:
        return
    top_n = 5
    cutoff = 0.0
    if branch_yes_no("Customize top_n / similarity_cutoff?", default=False):
        top_n_str = questionary.text("top_n:", default="5").ask()
        cutoff_str = questionary.text("similarity_cutoff:", default="0.0").ask()
        try:
            top_n = int(top_n_str or "5")
            cutoff = float(cutoff_str or "0.0")
        except ValueError:
            console.print("[red]invalid number; using defaults[/red]")

    try:
        res = client.indexes.search(
            kb_uuid,
            idx_uuid,
            SearchQuery(query=query, top_n=top_n, similarity_cutoff=cutoff),
        )
    except Exception as e:
        console.print(f"[red]search failed:[/red] {e}")
        return

    if not res.hits:
        console.print(
            "[dim](no hits — index may still be processing; check `pais index list`)[/dim]"
        )
        return

    table = Table(title=f"hits for: {query}")
    table.add_column("score", justify="right")
    table.add_column("origin")
    table.add_column("text")
    for hit in res.hits:
        snippet = (hit.text or "").strip().replace("\n", " ")
        if len(snippet) > 200:
            snippet = snippet[:200] + "…"
        table.add_row(f"{hit.score:.3f}", hit.origin_name or "—", snippet)
    console.print(table)


WORKFLOW = Workflow(
    name="Search an index (no LLM)",
    icon="🔎",
    description="Pick KB+index, query, render ranked hits.",
    run=run,
)
