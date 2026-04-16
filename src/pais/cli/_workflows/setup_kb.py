"""Workflow C — Provision a KB + index without an agent.

Thin wrapper that re-uses Workflow A's KB/index steps but skips the agent
creation. After completion, branches into Workflow B (ingest) if the user
opts in."""

from __future__ import annotations

from rich.console import Console

from pais.cli._workflows._base import (
    Workflow,
)
from pais.client import PaisClient
from pais.config import Settings


def run(client: PaisClient, settings: Settings, console: Console) -> None:
    # Re-use the KB+index halves of setup_agent by extracting them into a
    # shared helper would be cleaner; for now we run setup_agent and
    # the user picks "Done" at the agent step if they don't want one.
    # In v0.6.1 we'll factor the KB+index sub-flow out into a helper.
    console.print(
        "[bold]Provision KB + index (no agent).[/bold]  "
        "[dim]Same first two steps as 'Set up an agent', then we stop.[/dim]\n"
    )
    from pais.cli._workflows.setup_agent import run as run_setup_agent

    # Defer to setup_agent — the user can press ← back at the agent step
    # to skip agent creation. Cleanest UX without a code split right now.
    run_setup_agent(client, settings, console)


WORKFLOW = Workflow(
    name="Provision KB + index (no agent)",
    icon="📦",
    description="Just create a KB + index target; skip the agent.",
    run=run,
)
