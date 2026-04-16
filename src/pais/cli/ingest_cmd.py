"""`pais ingest`, `pais splitters`, `pais alias` subcommands."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from pais.cli import _alias
from pais.cli._config_file import load_profile_config
from pais.cli._flags import (
    DRY_RUN_OPT,
    HELP_OPTION_NAMES,
    OUTPUT_OPT,
    PROFILE_OPT,
    REPLACE_OPT,
    REPORT_OPT,
    SPLITTER_OPT,
    WORKERS_OPT,
)
from pais.cli._output import exit_code_for, render
from pais.client import PaisClient
from pais.config import Settings
from pais.errors import PaisError
from pais.ingest import SPLITTER_REGISTRY, get_splitter
from pais.ingest.runner import IngestReport, ingest_path, write_report

ingest_app = typer.Typer(
    help="Ingest files into a PAIS index.",
    invoke_without_command=False,
    context_settings=HELP_OPTION_NAMES,
)
splitters_app = typer.Typer(help="Inspect available splitters.", context_settings=HELP_OPTION_NAMES)
alias_app = typer.Typer(
    help="Inspect / clear the alias resolution cache.", context_settings=HELP_OPTION_NAMES
)


def _client() -> PaisClient:
    return Settings().build_client()


def _run(fn: Callable[[], None]) -> None:
    try:
        fn()
    except PaisError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=exit_code_for(e)) from e
    except typer.BadParameter:
        raise
    except Exception as e:
        typer.echo(f"error: {type(e).__name__}: {e}", err=True)
        raise typer.Exit(code=1) from e


@ingest_app.callback(invoke_without_command=True)
def ingest_root(
    target: str = typer.Argument(
        ..., metavar="<kb_ref>:<index_ref>", help="Target index. Both refs may be aliases or UUIDs."
    ),
    path: Path = typer.Argument(..., metavar="PATH", help="File or directory to ingest."),
    splitter_kind: str | None = SPLITTER_OPT,
    replace: bool = REPLACE_OPT,
    workers: int = WORKERS_OPT,
    dry_run: bool = DRY_RUN_OPT,
    report_path: Path = REPORT_OPT,
    output: str = OUTPUT_OPT,
) -> None:
    """Run a splitter over PATH and upload the chunks to <kb_ref>:<index_ref>."""

    def go() -> None:
        kb_ref, idx_ref = _alias.parse_index_ref(target)
        cfg, _, profile = load_profile_config()

        with _client() as c:
            kb_uuid, idx_uuid = _alias.resolve_index(c, profile, kb_ref, idx_ref, cfg=cfg)

            # Pick the splitter: --splitter override > config > error.
            kind = splitter_kind
            options = None
            if kind is None:
                # Look up index declaration in config.
                kb_decl = cfg.knowledge_bases.get(kb_ref) if cfg else None
                ix_decl = (
                    next((i for i in kb_decl.indexes if i.alias == idx_ref), None)
                    if kb_decl
                    else None
                )
                if ix_decl and ix_decl.splitter:
                    kind = ix_decl.splitter.kind
                    options = ix_decl.splitter.options()
                else:
                    raise typer.BadParameter(
                        "no splitter declared for this index in the config — "
                        "pass --splitter <kind> or add a [splitter] block to the index"
                    )
            if options is None:
                cls = get_splitter(kind)
                options = cls.options_model()
            cls = get_splitter(kind)
            splitter = cls(options)

            console = Console()
            files = list(path.rglob("*")) if path.is_dir() else [path]
            with Progress(
                TextColumn("[bold]ingest[/bold]"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("ingesting", total=sum(1 for f in files if f.is_file()))

                def on_file(_p: str) -> None:
                    progress.advance(task)

                report = ingest_path(
                    c,
                    path,
                    splitter=splitter,
                    kb_id=kb_uuid,
                    index_id=idx_uuid,
                    workers=workers,
                    replace=replace,
                    dry_run=dry_run,
                    progress=on_file,
                )

        write_report(report, report_path)
        _print_summary(report, report_path, output=output)
        if report.total_failed > 0:
            raise typer.Exit(code=2)

    _run(go)


def _print_summary(report: IngestReport, report_path: Path, *, output: str) -> None:
    summary = {
        "splitter_kind": report.splitter_kind,
        "total_files": report.total_files,
        "total_failed": report.total_failed,
        "total_chunks_uploaded": report.total_chunks_uploaded,
        "total_existing_deleted": report.total_existing_deleted,
        "chunk_size_distribution": report.chunk_size_distribution,
        "report": str(report_path),
    }
    render(summary, fmt=output)


# ----- splitters -----


@splitters_app.command("list")
def splitters_list(
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Include summary, input type, and typical chunk size."
    ),
    output: str = OUTPUT_OPT,
) -> None:
    """List every registered splitter kind. `-v` adds metadata columns."""
    from pais.ingest.splitters._base import meta_for

    rows: list[dict[str, object]] = []
    for k, cls in sorted(SPLITTER_REGISTRY.items()):
        m = meta_for(cls)
        row: dict[str, object] = {"kind": k, "summary": m.summary}
        if verbose:
            row["input"] = m.input_type
            row["chunk_size"] = m.typical_chunk_size
            row["unit"] = m.chunk_size_unit
        rows.append(row)
    cols = ["kind", "summary"]
    if verbose:
        cols += ["input", "chunk_size", "unit"]
    render(rows, fmt=output, columns=cols)


@splitters_app.command("show")
def splitters_show(kind: str, output: str = OUTPUT_OPT) -> None:
    """Show full metadata + option schema for one splitter kind."""
    from pais.ingest.splitters._base import meta_for

    def go() -> None:
        cls = get_splitter(kind)
        m = meta_for(cls)
        schema = cls.options_model.model_json_schema()
        if output == "table":
            _render_show_panel(cls, m, schema)
        else:
            render(
                {
                    "kind": cls.kind,
                    "class": cls.__name__,
                    "options_model": cls.options_model.__name__,
                    "meta": m.to_dict(),
                    "schema": schema,
                },
                fmt=output,
            )

    _run(go)


def _render_show_panel(cls: Any, meta: Any, schema: dict[str, Any]) -> None:
    """Rich-render a splitter's metadata + options table."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()

    # Header
    console.print(
        Panel.fit(
            f"[bold cyan]{cls.kind}[/bold cyan]\n[dim]{meta.summary}[/dim]",
            border_style="cyan",
        )
    )

    # Input
    console.print("\n[bold]Input[/bold]")
    console.print(f"  {meta.input_type}")
    console.print(f"  [dim]example: {meta.example_input}[/dim]")

    # Algorithm
    console.print("\n[bold]Algorithm[/bold]")
    for line in _wrap(meta.algorithm, width=78, indent="  "):
        console.print(line)

    # Output
    console.print("\n[bold]Output[/bold]")
    out_table = Table(show_header=False, box=None, pad_edge=False)
    out_table.add_column(style="bold cyan", no_wrap=True)
    out_table.add_column()
    out_table.add_row("unit", meta.chunk_size_unit)
    out_table.add_row("typical size", meta.typical_chunk_size)
    if meta.token_char_hint:
        out_table.add_row("token<->char", meta.token_char_hint)
    console.print(out_table)

    # Options
    props: dict[str, Any] = schema.get("properties") or {}
    if props:
        console.print("\n[bold]Options[/bold]")
        opt_table = Table()
        for col in ("field", "type", "default", "constraint", "description"):
            opt_table.add_column(col)
        for name, info in props.items():
            opt_table.add_row(
                name,
                str(info.get("type") or info.get("anyOf") or "?"),
                str(info.get("default", "—")),
                _constraint_summary(info),
                str(info.get("description") or ""),
            )
        console.print(opt_table)

    # Notes
    if meta.notes:
        console.print("\n[bold]Notes[/bold]")
        for n in meta.notes:
            console.print(f"  • {n}")


def _wrap(text: str, *, width: int, indent: str) -> list[str]:
    import textwrap

    return [indent + line for line in textwrap.wrap(text, width=width - len(indent)) or [""]]


def _constraint_summary(prop: dict[str, Any]) -> str:
    parts: list[str] = []
    if "minimum" in prop or "exclusiveMinimum" in prop:
        lo = prop.get("minimum", prop.get("exclusiveMinimum"))
        op = ">=" if "minimum" in prop else ">"
        parts.append(f"{op} {lo}")
    if "maximum" in prop or "exclusiveMaximum" in prop:
        hi = prop.get("maximum", prop.get("exclusiveMaximum"))
        op = "<=" if "maximum" in prop else "<"
        parts.append(f"{op} {hi}")
    return ", ".join(parts) or "—"


@splitters_app.command("preview")
def splitters_preview(
    kind: str = typer.Argument(..., help="Splitter kind to preview."),
    path: Path = typer.Argument(..., help="File or directory to split (dry-run; no upload)."),
    limit: int = typer.Option(100, "--limit", help="Max files when path is a directory."),
    max_bytes: int = typer.Option(
        50 * 1024 * 1024,
        "--max-bytes",
        help="Cap total bytes scanned (when path is a directory).",
    ),
    output: str = OUTPUT_OPT,
) -> None:
    """Run a splitter against a real file/dir (dry-run) and report the chunk distribution."""
    from rich.console import Console

    from pais.cli._splitter_preview import preview, render_panel

    def go() -> None:
        report = preview(kind, path, limit=limit, max_bytes=max_bytes)
        if output == "table":
            render_panel(report, Console())
        else:
            render(report.to_dict(), fmt=output)

    _run(go)


# ----- alias -----


@alias_app.command("list")
def alias_list(output: str = OUTPUT_OPT) -> None:
    """Print the alias resolution cache."""
    render(_alias.list_cache(), fmt=output)


@alias_app.command("clear")
def alias_clear(
    alias: str | None = typer.Argument(
        None, help="Specific alias to clear; omit to wipe everything."
    ),
    profile: str | None = PROFILE_OPT,
) -> None:
    """Invalidate cached UUID resolutions."""
    _alias.clear_cache(alias, profile=profile)
    typer.echo("cleared")
