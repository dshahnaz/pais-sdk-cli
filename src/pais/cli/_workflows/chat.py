"""Workflow E — Chat with an agent. Multi-line prompt loop; empty input exits."""

from __future__ import annotations

from typing import Any

import questionary
from rich.console import Console
from rich.panel import Panel

from pais.cli import _recent
from pais.cli._pickers import PickerContext, pick_agent
from pais.cli._prompts import CANCEL
from pais.cli._workflows._base import Workflow
from pais.client import PaisClient
from pais.config import Settings
from pais.models import ChatCompletionRequest, ChatMessage


def run(
    client: PaisClient,
    settings: Settings,
    console: Console,
    *,
    _preset: dict[str, Any] | None = None,
) -> None:
    profile = settings.profile or "default"
    if _preset and "agent_id" in _preset:
        agent_id = _preset["agent_id"]
    else:
        ctx = PickerContext(client=client, answers={}, profile=profile)
        pick = pick_agent(ctx)
        if pick is CANCEL:
            return
        agent_id = str(pick)

    _recent.record_use("agents", agent_id, profile=profile)

    if settings.mode == "mock":
        console.print(
            "[yellow][mock][/yellow] mock mode — answers are canned. "
            "Set PAIS_MODE=http for real LLM responses.\n"
        )
    console.print("[bold]Chat[/bold]  [dim](empty message ⏎ to exit)[/dim]\n")

    while True:
        question = questionary.text(
            "you:", multiline=True, instruction="(⏎⏎ to send, empty to exit)"
        ).ask()
        if not question or not question.strip():
            console.print("[dim]bye[/dim]")
            return
        try:
            with console.status("[dim]thinking…[/dim]", spinner="dots"):
                resp = client.agents.chat(
                    agent_id,
                    ChatCompletionRequest(
                        messages=[ChatMessage(role="user", content=question.strip())]
                    ),
                )
        except KeyboardInterrupt:
            console.print("[dim]aborted[/dim]")
            return
        except Exception as e:
            console.print(f"[red]error:[/red] {e}")
            continue
        text = resp.choices[0].message.content or ""
        console.print(Panel(text, title="agent", border_style="green"))


WORKFLOW = Workflow(
    name="Chat with an agent",
    icon="💬",
    description="Pick an agent and ask questions in a loop.",
    run=run,
)
