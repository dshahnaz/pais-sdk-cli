"""Workflow D — Apply pending TOML config.

Run the same drift check as `pais status`, then offer to apply with
`kb ensure`. For each newly-created index, branch into Workflow B."""

from __future__ import annotations

from collections.abc import Callable

from rich.console import Console

from pais.cli._config_file import load_profile_config
from pais.cli._workflows._base import (
    NextAction,
    Workflow,
    branch_yes_no,
    next_actions_menu,
)
from pais.cli.ensure_cmd import EnsureReport, _ensure_for_profile
from pais.client import PaisClient
from pais.config import Settings


def run(client: PaisClient, settings: Settings, console: Console) -> None:
    profile = settings.profile or "default"
    cfg, _, _ = load_profile_config()

    if not cfg.knowledge_bases:
        console.print(
            "[dim]No `[profiles.X.knowledge_bases.*]` declared in the active profile — "
            "nothing to apply.[/dim]"
        )
        return

    # Dry run first to show drift
    report = EnsureReport(profile=profile, dry_run=True)
    try:
        _ensure_for_profile(client, cfg, report=report, dry_run=True, prune=False)
    except Exception as e:
        console.print(f"[red]drift check failed:[/red] {e}")
        return

    pending = [r for r in report.rows if r.action != "existing"]
    if not pending:
        console.print("[green]✓ in sync — nothing to do[/green]")
        return

    console.print(f"[bold]{len(pending)} change(s) pending:[/bold]")
    for r in pending:
        console.print(f"  · {r.kind:5s} {r.alias:25s} {r.action}  {r.detail}")

    if not branch_yes_no(f"Apply these {len(pending)} change(s)?", default=True):
        return

    apply_report = EnsureReport(profile=profile, dry_run=False)
    _ensure_for_profile(client, cfg, report=apply_report, dry_run=False, prune=False)
    created = [r for r in apply_report.rows if r.action == "created"]
    console.print(f"[green]✓ applied {len(created)} change(s)[/green]")

    # Offer to ingest into newly-created indexes
    new_indexes = [r for r in created if r.kind == "index"]
    if not new_indexes:
        return

    actions: list[NextAction] = []

    def _make_cb(alias: str) -> Callable[[], None]:
        def _cb() -> None:
            console.print(
                f"[dim]→ run `pais ingest {alias} <path>` or pick 'Ingest data' from the menu[/dim]"
            )

        return _cb

    for r in new_indexes:
        actions.append(NextAction(label=f"📥  Ingest into {r.alias}", callback=_make_cb(r.alias)))
    actions.append(NextAction(label="✅  Done", callback=None))
    next_actions_menu(actions, console)


WORKFLOW = Workflow(
    name="Apply pending TOML config",
    icon="🔧",
    description="Run `kb ensure` after showing what will change.",
    run=run,
)
