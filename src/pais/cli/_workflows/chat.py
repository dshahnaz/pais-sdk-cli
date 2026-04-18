"""Workflow E — Chat with an agent. Multi-line prompt loop; empty input exits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import questionary
from rich.console import Console
from rich.panel import Panel

from pais.cli import _recent
from pais.cli._error_dump import dump_chat_error
from pais.cli._pickers import PickerContext, pick_agent
from pais.cli._prompts import CANCEL
from pais.cli._workflows._base import Workflow
from pais.client import PaisClient
from pais.config import Settings
from pais.models import ChatCompletionRequest, ChatMessage

_FILE_CMD = "/file "
# ~12 K tokens at 4 chars/token — past this, many model context windows are at risk.
_LARGE_FILE_BYTES = 50_000


def _maybe_load_file(raw: str, console: Console) -> str | None:
    """If `raw` starts with `/file <path>`, return the file's text (or None on error).
    Otherwise return `raw` unchanged. Caller treats None as "skip this turn".
    """
    if not raw.startswith(_FILE_CMD):
        return raw
    path_str = raw[len(_FILE_CMD) :].strip()
    if not path_str:
        console.print("[red]error:[/red] /file needs a path")
        return None
    path = Path(path_str).expanduser()
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as e:
        console.print(f"[red]error:[/red] {e}")
        return None
    size = len(text.encode("utf-8"))
    if size > _LARGE_FILE_BYTES:
        console.print(
            f"[yellow]warning:[/yellow] {path.name} is {size // 1024} KB (~{size // 4000}K tokens) — "
            f"may exceed the model's context window; response can come back empty"
        )
    console.print(f"[dim]loaded {size} bytes from {path.name}[/dim]")
    return text


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
            "you:",
            multiline=True,
            instruction="(⏎⏎ to send, /file <path> to load, empty to exit)",
        ).ask()
        if not question or not question.strip():
            console.print("[dim]bye[/dim]")
            return
        content = _maybe_load_file(question.strip(), console)
        if content is None:
            continue
        try:
            with console.status("[dim]thinking…[/dim]", spinner="dots"):
                resp = client.agents.chat(
                    agent_id,
                    ChatCompletionRequest(messages=[ChatMessage(role="user", content=content)]),
                )
        except KeyboardInterrupt:
            console.print("[dim]aborted[/dim]")
            return
        except Exception as e:
            console.print(f"[red]error:[/red] {e}")
            try:
                dump_path = dump_chat_error(e, agent_id=agent_id, prompt=content, profile=profile)
                console.print(f"[dim]full detail → {dump_path}[/dim]")
            except Exception as dump_exc:
                console.print(f"[dim](could not save error dump: {dump_exc})[/dim]")
            continue
        choice = resp.choices[0]
        text = choice.message.content or ""
        usage = resp.usage
        footer = (
            f"finish_reason={choice.finish_reason}  "
            f"tokens in={usage.prompt_tokens} out={usage.completion_tokens} total={usage.total_tokens}"
        )
        if not text.strip():
            console.print(
                Panel(
                    f"[yellow](empty response)[/yellow]\n[dim]{footer}[/dim]\n\n"
                    f"Common causes: prompt exceeded the model's context window "
                    f"(finish_reason=length), content filter tripped "
                    f"(finish_reason=content_filter), or the backend truncated to zero tokens. "
                    f"Try a shorter prompt or a different model.",
                    title="agent",
                    border_style="yellow",
                )
            )
        else:
            console.print(Panel(text, title="agent", border_style="green"))
            console.print(f"[dim]{footer}[/dim]")


WORKFLOW = Workflow(
    name="Chat with an agent",
    icon="💬",
    description="Pick an agent and ask questions in a loop.",
    run=run,
)
