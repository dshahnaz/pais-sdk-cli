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
from pais.cli._workflows._base import (
    BACK,
    FieldSpec,
    ReviewSpec,
    Workflow,
    prompt_review_screen,
)
from pais.cli._workflows._base import (
    CANCEL as REVIEW_CANCEL,
)
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

    # Review screen: both knobs visible with sensible defaults. Press Enter
    # on "Go" to accept and run; pick "Edit …" only if you actually want to
    # change one. No more "customize? y/n" gate.
    review = ReviewSpec(
        title=f"🔎 Search — review (query: {query!r})",
        fields=[
            FieldSpec(
                name="top_n",
                value=5,
                hint="how many hits to return (default 5)",
                re_prompt=_reprompt_int("top_n", 5),
            ),
            FieldSpec(
                name="similarity_cutoff",
                value=0.0,
                hint="drop hits below this score; 0.0 = keep everything",
                re_prompt=_reprompt_float("similarity_cutoff", 0.0),
            ),
        ],
    )
    result = prompt_review_screen(review, console)
    if result is BACK or result is REVIEW_CANCEL:
        return
    assert isinstance(result, dict)
    top_n = int(result["top_n"])
    cutoff = float(result["similarity_cutoff"])

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


def _reprompt_int(name: str, fallback: int):  # type: ignore[no-untyped-def]
    def _go(current: Any) -> int:
        ans = questionary.text(
            f"{name}:",
            default=str(current),
            validate=lambda v: v.strip().lstrip("-").isdigit() or "must be an integer",
        ).ask()
        if ans is None or ans.strip() == "":
            return int(current) if current is not None else fallback
        return int(ans)

    return _go


def _reprompt_float(name: str, fallback: float):  # type: ignore[no-untyped-def]
    def _go(current: Any) -> float:
        ans = questionary.text(
            f"{name}:",
            default=str(current),
            validate=lambda v: _is_float(v) or "must be a number (e.g. 0.0, 0.35)",
        ).ask()
        if ans is None or ans.strip() == "":
            return float(current) if current is not None else fallback
        return float(ans)

    return _go


def _is_float(v: str) -> bool:
    try:
        float(v)
    except ValueError:
        return False
    return True


WORKFLOW = Workflow(
    name="Search an index (no LLM)",
    icon="🔎",
    description="Pick KB+index, query, render ranked hits.",
    run=run,
)
