"""Interactive shell for `pais` — a context-aware menu over the typer command tree.

`enter_interactive(app)` boots the menu when `pais` is invoked with no args
(and stdin is a TTY). The menu walks the live typer tree via `_introspect`,
shows one-line descriptions, lets the user drill in or filter by typing,
prompts for required args (with `_pickers` providing live KB/index/agent
selection lists), then dispatches the underlying command callback in-process
and returns to the menu.

Design notes:
- Group titles are flat: `kb`, `kb list`, `kb show`, … No nested submenus —
  one big filterable list. Faster to navigate, fewer keystrokes.
- Destructive ops (`*_delete`, `*_purge`, `index cancel`, `kb ensure --prune`)
  get a single confirm prompt up-front showing the resolved label, then we
  auto-pass `yes=True` so the underlying command doesn't double-prompt.
- Errors land in the same `_run` wrapper as the non-interactive path —
  `PaisError` exits with the same code; other exceptions are caught and
  printed without crashing the menu loop.
"""

from __future__ import annotations

import sys
from typing import Any

import questionary
import typer
from rich.console import Console

from pais.cli._introspect import CommandSpec, ParamSpec, walk
from pais.cli._pickers import PickerContext, picker_for
from pais.cli._prompts import CANCEL, prompt_for_param
from pais.cli._workflows._base import (
    BACK,
    FieldSpec,
    ReviewSpec,
    prompt_review_screen,
)
from pais.cli._workflows._base import (
    CANCEL as REVIEW_CANCEL,
)
from pais.config import Settings
from pais.errors import PaisError

# Commands that require a confirm prompt before dispatch + auto-pass yes=True.
_DESTRUCTIVE: frozenset[tuple[str, ...]] = frozenset(
    {
        ("kb", "delete"),
        ("kb", "purge"),
        ("index", "delete"),
        ("index", "purge"),
        ("index", "cancel"),
        ("agent", "delete"),
    }
)

_QUIT = "⏏  quit"


def enter_interactive(app: typer.Typer) -> None:
    """Run the menu loop until the user picks Quit (or hits Ctrl-C).

    v0.6 wraps the v0.5 flat command menu with a smart landing screen:
    bare entry shows env state + a recommended workflow; the user picks a
    workflow (orchestrated multi-step flow) OR falls through to the flat
    typer command list.

    v0.6.2: defaults to WARNING-level logs (the per-request `pais.request`
    INFO lines are noise inside an interactive session). `pais -v` or
    `PAIS_VERBOSE=1` lifts the floor back to INFO for troubleshooting.
    """
    import os as _os

    from pais.cli._landing import show_landing
    from pais.logging import configure_logging

    console = Console()
    settings = Settings()

    # Respect the -v / -vv tier chosen in app.py's root callback. PAIS_VERBOSE
    # is "1" for INFO, "2" for DEBUG, or absent for WARNING.
    #   1. Mutate `settings.log_level` so every subsequent `from_settings`
    #      call inside the loop applies the same floor.
    #   2. Run `configure_logging` once now so logging takes effect before
    #      the first client is built (covers the TLS-warning window).
    _tier = _os.environ.get("PAIS_VERBOSE", "0")
    _level = "WARNING" if _tier == "0" else ("INFO" if _tier == "1" else "DEBUG")
    settings.log_level = _level
    configure_logging(
        level=_level,
        log_file=settings.log_file,
        json_console=settings.log_json_console,
    )

    while True:
        try:
            with settings.build_client() as client:
                workflow = show_landing(client, settings, console)
        except KeyboardInterrupt:
            console.print("[dim]bye[/dim]")
            return
        if workflow is None:
            # User picked "📋 all commands…" (or hit Esc on landing) — fall
            # through to the v0.5 flat menu for one selection, then loop back.
            specs = walk(app)
            choice = _select_command(specs)
            if choice is None:
                console.print("[dim]bye[/dim]")
                return
            try:
                _dispatch(choice, settings, console)
            except KeyboardInterrupt:
                console.print("\n[dim]aborted; back to menu[/dim]\n")
            except PaisError as e:
                console.print(f"[red]error:[/red] {e}\n")
            except Exception as e:  # pragma: no cover
                console.print(f"[red]error:[/red] {type(e).__name__}: {e}\n")
            continue

        # Run the chosen workflow
        try:
            with settings.build_client() as client:
                workflow.run(client, settings, console)
        except KeyboardInterrupt:
            console.print("\n[dim]aborted; back to menu[/dim]\n")
        except PaisError as e:
            console.print(f"[red]error:[/red] {e}\n")
        except Exception as e:  # pragma: no cover
            console.print(f"[red]error:[/red] {type(e).__name__}: {e}\n")


# ----- menu selection ---------------------------------------------------------


def _select_command(specs: list[CommandSpec]) -> CommandSpec | None:
    """Show one big filterable list of every leaf command. Return the chosen
    spec, or None if the user picked Quit."""
    titles: list[str] = [_QUIT]
    by_title: dict[str, CommandSpec] = {}
    for s in specs:
        title = f"{s.display:24s}  {s.help or '—'}"
        titles.append(title)
        by_title[title] = s
    pick = questionary.select(
        "command:", choices=titles, use_search_filter=True, use_jk_keys=False
    ).ask()
    if pick is None or pick == _QUIT:
        return None
    return by_title[pick]


# ----- dispatch ---------------------------------------------------------------


def _dispatch(spec: CommandSpec, settings: Settings, console: Console) -> None:
    """Prompt for every parameter, confirm if destructive, then call the callback.

    Two-phase:
      1. Required params and pickers are resolved inline — the user needs to
         choose a KB, an index, an agent, etc. before anything else.
      2. Optional params are shown together in one review screen with all
         defaults pre-filled. Enter on "Go" runs with all defaults; "Edit X"
         pops a prompt for just that one field. No more per-param
         "customize --X?" yes/no gate.
    """
    answers: dict[str, Any] = {}
    optional_fields: list[tuple[ParamSpec, FieldSpec]] = []

    # Build a fresh client per dispatch so transport state is clean.
    with settings.build_client() as client:
        ctx = PickerContext(client=client, answers=answers, profile=settings.profile or "default")

        is_destructive = spec.path in _DESTRUCTIVE
        for param in spec.params:
            # Hidden params are for scripted use only — keep them callable via
            # flags but skip the interactive prompt so the shell stays quiet.
            # Inject the declared default so the callback receives the real
            # value (e.g. None), not typer's raw OptionInfo wrapper — otherwise
            # `if param:` branches see a truthy OptionInfo and explode downstream.
            if param.hidden:
                answers[param.name] = param.default
                continue
            # The destructive confirm below auto-injects `yes=True`; don't
            # prompt the user about it separately.
            if is_destructive and param.name == "yes":
                continue

            picker = picker_for(spec.path, param.name)
            if picker is not None:
                value = picker(ctx)
                if value is CANCEL:
                    console.print("[dim]aborted; back to menu[/dim]\n")
                    return
                answers[param.name] = value
                continue

            if param.required:
                value = prompt_for_param(param)
                if value is CANCEL:
                    console.print("[dim]aborted; back to menu[/dim]\n")
                    return
                answers[param.name] = value
                continue

            # Optional — collect for the review screen.
            optional_fields.append(
                (
                    param,
                    FieldSpec(
                        name=param.name,
                        value=param.default,
                        hint=_hint_for(param),
                        re_prompt=_reprompt_for(param),
                    ),
                )
            )

        if optional_fields:
            review = ReviewSpec(
                title=f"→ {spec.display} — options (Enter = run with defaults)",
                fields=[fs for _, fs in optional_fields],
            )
            result = prompt_review_screen(review, console)
            if result is REVIEW_CANCEL or result is BACK:
                console.print("[dim]aborted; back to menu[/dim]\n")
                return
            assert isinstance(result, dict)
            for param, fs in optional_fields:
                # The re_prompt may have returned CANCEL for a specific field,
                # which `prompt_review_screen` surfaces as unchanged value.
                # Pass the current value through unchanged.
                answers[param.name] = result.get(fs.name, param.default)

        if is_destructive:
            label = _confirmation_label(spec, answers)
            if not questionary.confirm(
                f"Really {' '.join(spec.path)} {label}?", default=False
            ).ask():
                console.print("[dim]aborted; back to menu[/dim]\n")
                return
            if "yes" in {p.name for p in spec.params}:
                answers["yes"] = True

    # Call the callback outside the client `with` so the command can build its
    # own client (the dispatched commands all do `with _client() as c: ...`).
    console.print(f"\n[bold]→ {spec.display}[/bold]\n")
    try:
        spec.callback(**answers)
    except typer.Exit as e:
        if e.exit_code:
            console.print(f"[yellow](command exited with code {e.exit_code})[/yellow]")
    console.print()


def _hint_for(param: ParamSpec) -> str | None:
    """One-line hint for the review screen — first line of the help text."""
    if not param.help:
        return None
    return param.help.splitlines()[0].strip() or None


def _reprompt_for(param: ParamSpec):  # type: ignore[no-untyped-def]
    """Return a callable the review screen invokes when the user picks
    'Edit <name>'. Re-uses the type-aware `prompt_for_param` so widgets match
    the param's annotation."""

    def _go(_current: Any) -> Any:
        val = prompt_for_param(param)
        if val is CANCEL:
            return _current
        return val

    return _go


_CONFIRM_LABEL_SKIP: frozenset[str] = frozenset({"yes", "output", "epoch"})


def _confirmation_label(spec: CommandSpec, answers: dict[str, Any]) -> str:
    """Render the resolved args+options for the confirm prompt so the user
    sees exactly which resource they're about to mutate.

    Includes every answered parameter — positional arguments and options —
    except a small skip list of presentation-only flags (`yes`, `output`,
    `epoch`). This is deliberately wider than typer's argument-vs-option
    classification: pickers answer param names like `kb_id` / `kb_ref` which
    may be reported either way depending on how the signature is introspected,
    and the point of the label is to surface the resource reference."""
    known = {p.name for p in spec.params}
    parts = [
        f"{k}={v!r}"
        for k, v in answers.items()
        if k in known and k not in _CONFIRM_LABEL_SKIP and v is not None
    ]
    return " ".join(parts) or "(no args)"


# ----- module-level entry shared by app.py + shell_cmd.py ---------------------


def run_or_exit(app: typer.Typer, *, force: bool = False) -> None:
    """Entry point used by both the bare-`pais` trigger and `pais shell`.

    `force=True` skips the TTY check (so `pais shell` works in pseudo-terminals
    where stdin detection is unreliable; bare `pais` keeps the TTY guard so
    `pais | head` doesn't hang)."""
    if not force and not sys.stdin.isatty():
        return  # caller falls through to the help banner
    enter_interactive(app)


# Re-exported so app.py can wire it without importing the long name.
__all__ = ["enter_interactive", "run_or_exit"]
