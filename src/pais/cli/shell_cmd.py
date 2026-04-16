"""`pais shell` — explicit entry into the interactive menu.

Bare `pais` already drops into the menu when stdin is a TTY; `pais shell`
forces it on (useful inside pseudo-terminals where TTY detection is flaky,
or when the user just wants to be explicit about intent)."""

from __future__ import annotations

import sys

import typer

from pais.cli.interactive import enter_interactive


def shell() -> None:
    """Open the interactive menu (forced — works regardless of TTY detection)."""
    if not sys.stdin.isatty():
        typer.echo("pais shell: refusing to start without a TTY on stdin", err=True)
        raise typer.Exit(code=1)
    # Late import so the shared app is fully built.
    from pais.cli.app import app as cli_app

    enter_interactive(cli_app)
