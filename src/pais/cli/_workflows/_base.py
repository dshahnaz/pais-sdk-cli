"""Workflow primitives: dataclass + the four UX helpers every workflow uses.

* `Workflow`            — registry entry (name/icon/description/run)
* `branch_yes_no`       — Esc-aware yes/no with default highlight
* `done_banner`         — green ✓ summary block
* `prompt_review_screen`— single-screen Go/Edit/Back review of N defaults
* `next_actions_menu`   — post-success "what next?" picker
* `confirm_by_typing`   — type-the-resource-name destructive confirm
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pais.client import PaisClient
from pais.config import Settings

BACK = object()  # sentinel returned by review screen when user picks ← back
CANCEL = object()  # sentinel — user aborted entirely

# ----- Workflow dataclass ----------------------------------------------------


@dataclass
class Workflow:
    """One entry in the WORKFLOWS registry. `run()` is invoked from the menu."""

    name: str
    icon: str
    description: str
    run: Callable[[PaisClient, Settings, Console], None]
    requires_tty: bool = True

    @property
    def menu_title(self) -> str:
        return f"{self.icon}  {self.name}"


# ----- branch_yes_no ---------------------------------------------------------


def branch_yes_no(question: str, *, default: bool = False) -> bool:
    """questionary.confirm with consistent styling. Returns False on Esc/Ctrl-C."""
    ans = questionary.confirm(question, default=default).ask()
    return bool(ans) if ans is not None else False


# ----- done_banner -----------------------------------------------------------


def done_banner(console: Console, title: str, summary: dict[str, Any]) -> None:
    """Render a green ✓ panel — use ONLY when the operation verifiably succeeded."""
    body_lines = [f"[bold]{k}[/bold]  {v}" for k, v in summary.items()]
    console.print(
        Panel.fit(
            "\n".join(body_lines),
            title=f"[green]✓ {title}[/green]",
            border_style="green",
        )
    )


def error_banner(console: Console, title: str, summary: dict[str, Any]) -> None:
    """Render a red ✗ panel — use when the operation failed (verified)."""
    body_lines = [f"[bold]{k}[/bold]  {v}" for k, v in summary.items()]
    console.print(
        Panel.fit(
            "\n".join(body_lines),
            title=f"[red]✗ {title}[/red]",
            border_style="red",
        )
    )


def partial_banner(console: Console, title: str, summary: dict[str, Any]) -> None:
    """Render a yellow ⚠ panel — use when the operation partially succeeded
    or completed with warnings (e.g. couldn't verify state after the call)."""
    body_lines = [f"[bold]{k}[/bold]  {v}" for k, v in summary.items()]
    console.print(
        Panel.fit(
            "\n".join(body_lines),
            title=f"[yellow]⚠ {title}[/yellow]",
            border_style="yellow",
        )
    )


# ----- prompt_review_screen --------------------------------------------------


@dataclass
class FieldSpec:
    """One field in a review screen."""

    name: str
    value: Any
    hint: str | None = None  # one-liner shown under the value
    editable: bool = True
    re_prompt: Callable[[Any], Any] | None = None  # called when user picks Edit


@dataclass
class ReviewSpec:
    """A bundle of fields shown in a single review screen."""

    title: str
    fields: list[FieldSpec] = field(default_factory=list)


def prompt_review_screen(spec: ReviewSpec, console: Console) -> dict[str, Any] | object:
    """Render a key-value table; user picks Go / Edit <field> / ← back.

    Returns:
      - dict of {field_name: value} on Go (committed)
      - BACK sentinel if user picked ← back
      - CANCEL sentinel on Esc / Ctrl-C
    """
    while True:
        # 1) render the table
        table = Table(show_header=False, box=None, pad_edge=False)
        table.add_column(style="bold cyan", no_wrap=True)
        table.add_column()
        for f in spec.fields:
            value_text = _fmt(f.value)
            table.add_row(f.name, value_text)
            if f.hint:
                table.add_row("", f"[dim]↑ {f.hint}[/dim]")
        console.print(Panel(table, title=spec.title, border_style="cyan"))

        # 2) action picker
        choices: list[str] = ["✅ Go (commit)"]
        for f in spec.fields:
            if f.editable:
                choices.append(f"✏  Edit {f.name}")
        choices.append("← back")
        pick = questionary.select(
            "action:", choices=choices, instruction="Ctrl-C / Esc → back"
        ).ask()
        if pick is None:
            return CANCEL
        if pick == "← back":
            return BACK
        if pick.startswith("✅"):
            return {f.name: f.value for f in spec.fields}
        # Edit one field
        target = pick.removeprefix("✏  Edit ").strip()
        target_field = next((f for f in spec.fields if f.name == target), None)
        if target_field is None:
            continue
        new_val: Any
        if target_field.re_prompt is not None:
            new_val = target_field.re_prompt(target_field.value)
        else:
            ans = questionary.text(f"{target}:", default=_fmt(target_field.value)).ask()
            if ans is None:
                continue
            new_val = ans
        if new_val is CANCEL:
            continue
        target_field.value = new_val


# ----- next_actions_menu -----------------------------------------------------


@dataclass
class NextAction:
    label: str
    callback: Callable[[], None] | None  # None = "Done" (just returns)
    annotation: str | None = None
    recommended: bool = False


def next_actions_menu(actions: list[NextAction], console: Console) -> None:
    """Post-success "what next?" — recommended item shown first; picking 'Done' returns."""
    titles: list[str] = []
    by_title: dict[str, NextAction] = {}
    for a in actions:
        prefix = "→ " if a.recommended else "  "
        ann = f"  [dim]({a.annotation})[/dim]" if a.annotation else ""
        title = f"{prefix}{a.label}{ann}"
        titles.append(title)
        by_title[title] = a
    pick = questionary.select(
        "What next?", choices=titles, instruction="Ctrl-C → back to menu"
    ).ask()
    if pick is None:
        return
    chosen = by_title[pick]
    if chosen.callback:
        try:
            chosen.callback()
        except KeyboardInterrupt:
            console.print("[dim]aborted[/dim]")


# ----- confirm_by_typing -----------------------------------------------------


def confirm_by_typing(label: str, *, expected: str) -> bool:
    """GitHub-style: user must type the resource name exactly to proceed.

    Honours the `--quick-confirm` flag (via PAIS_QUICK_CONFIRM env) — when set,
    falls back to a plain y/N for power users who'd rather take the keystroke
    risk than type the whole name.
    """
    if os.environ.get("PAIS_QUICK_CONFIRM"):
        return branch_yes_no(label, default=False)
    typed = questionary.text(
        f"{label}\nType '{expected}' to confirm (anything else cancels):",
        instruction="Ctrl-C → cancel",
    ).ask()
    return bool(typed == expected)


# ----- helpers ---------------------------------------------------------------


def _fmt(v: Any) -> str:
    if v is None:
        return "(none)"
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)
