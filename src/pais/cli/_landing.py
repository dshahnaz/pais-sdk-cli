"""Smart landing screen for the v0.6 interactive shell.

Reads the live environment (KB / index / agent counts + drift), suggests
the most-relevant next action, then shows a compact menu the user can
filter by typing OR drive with single-key shortcuts."""

from __future__ import annotations

from dataclasses import dataclass

import questionary
from rich.console import Console

from pais.cli._config_file import load_profile_config
from pais.cli._workflows import WORKFLOWS, Workflow
from pais.cli.ensure_cmd import EnsureReport, _ensure_for_profile
from pais.client import PaisClient
from pais.config import Settings
from pais.errors import PaisError

_FALLBACK_FLAT_MENU = "📋  all commands…"


@dataclass
class EnvSnapshot:
    kb_count: int
    index_count: int
    agent_count: int
    drift_count: int
    error: str | None = None


def gather(client: PaisClient, profile: str) -> EnvSnapshot:
    """One-shot fetch of the env state. Errors are absorbed — the menu
    still renders, just without counts."""
    try:
        kbs = client.knowledge_bases.list().data
    except PaisError as e:
        return EnvSnapshot(0, 0, 0, 0, error=str(e))
    import contextlib

    index_count = 0
    for kb in kbs:
        with contextlib.suppress(PaisError):
            index_count += len(client.indexes.list(kb.id).data)
    try:
        agents = client.agents.list().data
        agent_count = len(agents)
    except PaisError:
        agent_count = 0

    # Drift check (best-effort)
    drift_count = 0
    try:
        cfg, _, _ = load_profile_config()
        if cfg.knowledge_bases:
            report = EnsureReport(profile=profile, dry_run=True)
            _ensure_for_profile(client, cfg, report=report, dry_run=True, prune=False)
            drift_count = sum(1 for r in report.rows if r.action != "existing")
    except Exception:
        pass

    return EnvSnapshot(
        kb_count=len(kbs),
        index_count=index_count,
        agent_count=agent_count,
        drift_count=drift_count,
    )


def suggest(snap: EnvSnapshot) -> Workflow:
    """Pick the workflow most relevant to the current env state."""
    by_name = {w.name: w for w in WORKFLOWS}
    if snap.drift_count > 0:
        return by_name["Apply pending TOML config"]
    if snap.agent_count == 0:
        return by_name["Set up a chat agent over my docs"]
    if snap.index_count > 0 and snap.agent_count > 0:
        return by_name["Chat with an agent"]
    return by_name["Set up a chat agent over my docs"]


def show_landing(client: PaisClient, settings: Settings, console: Console) -> Workflow | None:
    """Render the landing screen and return the chosen workflow (or None for
    fallback to the flat command menu)."""
    profile = settings.profile or "default"
    mode_colour = "green" if settings.mode == "http" else "red"
    mode_label = "real PAIS" if settings.mode == "http" else "in-process mock"

    console.print(
        f"[bold]profile[/bold]=[cyan]{profile}[/cyan]  ·  "
        f"[bold]mode[/bold]=[{mode_colour}]{settings.mode}[/{mode_colour}] "
        f"[dim]({mode_label})[/dim]"
    )

    with console.status("[dim]reading environment…[/dim]", spinner="dots"):
        snap = gather(client, profile)

    if snap.error:
        console.print(f"[yellow]server: {snap.error}[/yellow]")
        snap_line = "[dim]server unreachable; menu still works[/dim]"
    else:
        drift_str = (
            f"[yellow]· ⚠ {snap.drift_count} drift[/yellow]" if snap.drift_count else "· 0 drift"
        )
        snap_line = (
            f"[bold]{snap.kb_count}[/bold] KBs  ·  "
            f"[bold]{snap.index_count}[/bold] indexes  ·  "
            f"[bold]{snap.agent_count}[/bold] agents  {drift_str}"
        )
    console.print(snap_line + "\n")

    recommended = suggest(snap)
    console.print(f"[dim]recommended:[/dim] {recommended.icon}  [bold]{recommended.name}[/bold]\n")

    # Build the menu — recommended first, then the rest, then ⋯ more, then flat fallback.
    titles: list[str] = []
    by_title: dict[str, Workflow] = {}
    rec_title = f"→ {recommended.icon}  {recommended.name}"
    titles.append(rec_title)
    by_title[rec_title] = recommended
    for w in WORKFLOWS:
        if w.name == recommended.name:
            continue
        t = f"  {w.icon}  {w.name}"
        titles.append(t)
        by_title[t] = w
    titles.append(_FALLBACK_FLAT_MENU)

    log_path = settings.log_file or "~/.pais/logs/pais.log"
    console.print(f"[dim]logs: {log_path}  ·  pais -v for full stream  ·  pais logs tail[/dim]\n")

    pick = questionary.select(
        "command:",
        choices=titles,
        use_search_filter=True,
        use_jk_keys=False,
        instruction="type to filter · Ctrl-C → exit shell",
    ).ask()
    if pick is None:
        return None
    if pick == _FALLBACK_FLAT_MENU:
        return None  # caller drops to the v0.5 flat menu
    return by_title[pick]
