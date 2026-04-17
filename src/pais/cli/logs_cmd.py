"""`pais logs` — inspect / tail / clear the structured log file."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from pais.cli._flags import HELP_OPTION_NAMES, YES_OPT
from pais.config import Settings

app = typer.Typer(help="Inspect and tail the PAIS log file.", context_settings=HELP_OPTION_NAMES)


@app.command("path")
def logs_path() -> None:
    """Print the active log file path (for `tail -f $(pais logs path)`)."""
    s = Settings()
    typer.echo(str(s.log_file) if s.log_file else "(no log file configured)")


@app.command("tail")
def logs_tail(
    n: int = typer.Option(50, "-n", help="Number of lines to show."),
    follow: bool = typer.Option(False, "-f", "--follow", help="Follow the file (Ctrl-C to stop)."),
) -> None:
    """Print the last N lines of the log file. `-f` follows (TTY only)."""
    s = Settings()
    if not s.log_file:
        typer.echo("no log file configured", err=True)
        raise typer.Exit(code=1)
    log = Path(s.log_file).expanduser()
    if not log.exists():
        typer.echo(f"log file not found: {log}", err=True)
        raise typer.Exit(code=1)
    if follow:
        if not sys.stdin.isatty():
            typer.echo("pais logs tail -f: refusing without a TTY on stdin", err=True)
            raise typer.Exit(code=1)
        import subprocess

        subprocess.run(["tail", "-n", str(n), "-f", str(log)], check=False)
        return
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-n:]:
        typer.echo(line)


@app.command("clear")
def logs_clear(yes: bool = YES_OPT) -> None:
    """Truncate the active log file. Rotated backups (.1, .2, .3) stay."""
    s = Settings()
    if not s.log_file:
        typer.echo("no log file configured", err=True)
        raise typer.Exit(code=1)
    log = Path(s.log_file).expanduser()
    if not log.exists():
        typer.echo(f"nothing to clear: {log}", err=True)
        raise typer.Exit(code=1)
    if not yes:
        if not sys.stdin.isatty():
            typer.echo("refusing without --yes (non-interactive)", err=True)
            raise typer.Exit(code=1)
        if not typer.confirm(f"truncate {log}?", default=False):
            raise typer.Exit()
    log.write_text("")
    typer.echo(f"cleared {log}")
