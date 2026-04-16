"""`pais config` subcommand: init/show/path."""

from __future__ import annotations

from pathlib import Path

import typer

from pais.cli._config_file import (
    GLOBAL_PATH,
    PROJECT_FILENAME,
    SCAFFOLD_GLOBAL,
    SCAFFOLD_PROJECT,
    ConfigError,
    discover_config_path,
    load_profile,
)
from pais.cli._flags import FORCE_OPT, HELP_OPTION_NAMES, OUTPUT_OPT, PROFILE_OPT
from pais.cli._output import render
from pais.config import Settings

app = typer.Typer(
    help="Inspect and scaffold the PAIS config file.", context_settings=HELP_OPTION_NAMES
)


@app.command("init")
def init(
    project: bool = typer.Option(
        False, "--project", help=f"Create ./{PROJECT_FILENAME} instead of ~/.pais/config.toml"
    ),
    force: bool = FORCE_OPT,
) -> None:
    """Scaffold a config file with sensible comments."""
    target = Path.cwd() / PROJECT_FILENAME if project else GLOBAL_PATH
    if target.exists() and not force:
        typer.echo(f"refusing to overwrite {target} (use --force)", err=True)
        raise typer.Exit(code=1)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(SCAFFOLD_PROJECT if project else SCAFFOLD_GLOBAL)
    typer.echo(f"wrote {target}")


@app.command("show")
def show(
    profile: str | None = PROFILE_OPT,
    config: Path | None = typer.Option(None, "--config"),
    output: str = OUTPUT_OPT,
) -> None:
    """Print effective Settings (secrets redacted)."""
    from pais.config import set_runtime_overrides

    set_runtime_overrides(config_path=config, profile=profile)
    # Surface config-file errors before they bubble up as tracebacks.
    try:
        load_profile(path=config, profile=profile)
    except ConfigError as e:
        typer.echo(f"config error: {e}", err=True)
        raise typer.Exit(code=1) from e
    s = Settings()
    data = s.model_dump(mode="json")
    for secret in ("password", "client_secret", "bearer_token"):
        if data.get(secret):
            data[secret] = "***"
    render(data, fmt=output)


@app.command("path")
def path_cmd(
    config: Path | None = typer.Option(None, "--config"),
    profile: str | None = PROFILE_OPT,
    output: str = OUTPUT_OPT,
) -> None:
    """Show which config file (if any) and which profile resolve right now."""
    p = discover_config_path(config)
    try:
        _data, used_path, used_profile = load_profile(path=config, profile=profile)
    except ConfigError as e:
        # Don't dump a stack — show what file is broken so the user can fix it.
        typer.echo(f"config error: {e}", err=True)
        raise typer.Exit(code=1) from e
    render(
        {
            "config_file": str(used_path) if used_path else None,
            "discovered_path": str(p) if p else None,
            "profile": used_profile,
        },
        fmt=output,
    )
