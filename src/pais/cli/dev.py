"""`pais-dev` CLI: split + ingest test-suite markdown files into a PAIS KB."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from pais.cli._output import exit_code_for, render
from pais.client import PaisClient
from pais.config import Settings
from pais.dev.ingest import IngestReport, ingest_directory, ingest_file, write_report
from pais.dev.split_suite import split_suite, write_sections
from pais.errors import PaisError

app = typer.Typer(
    help="Developer commands for PAIS: split and ingest structured markdown test suites."
)


def _print_version_and_exit(value: bool) -> None:
    if value:
        from pais import __version__

        typer.echo(f"pais-dev {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        is_eager=True,
        callback=_print_version_and_exit,
        help="Show version and exit",
    ),
) -> None:
    """pais-dev developer commands."""


_OutputOpt = typer.Option("table", "--output", "-o", help="table | json | yaml")
_OutDirOpt = typer.Option(Path("./out"), "--out", help="Output directory")
_ReportOpt = typer.Option(
    Path("./ingest-report.json"), "--report", help="Where to write the JSON report"
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


@app.command("split-suite")
def split_suite_cmd(
    file: Path,
    out: Path = _OutDirOpt,
    output: str = _OutputOpt,
) -> None:
    """Split one markdown suite file into per-section files on disk."""

    def go() -> None:
        sections = split_suite(file)
        paths = write_sections(sections, out)
        summary = {
            "suite": sections[0].suite_name if sections else "",
            "sections_emitted": len(sections),
            "out_dir": str(out),
            "files": [str(p) for p in paths],
        }
        render(summary, fmt=output)

    _run(go)


@app.command("ingest-suite")
def ingest_suite_cmd(
    file: Path,
    kb: str = typer.Option(..., "--kb", help="Knowledge base id"),
    index: str = typer.Option(..., "--index", help="Index id"),
    output: str = _OutputOpt,
) -> None:
    """Split one suite and upload every section to PAIS."""

    def go() -> None:
        with _client() as c:
            result = ingest_file(c, file, kb_id=kb, index_id=index)
        render(
            {
                "suite": result.suite_name,
                "sections_emitted": result.sections_emitted,
                "sections_uploaded": result.sections_uploaded,
                "errors": result.errors,
            },
            fmt=output,
        )
        if result.errors:
            raise typer.Exit(code=2)

    _run(go)


@app.command("ingest-suites")
def ingest_suites_cmd(
    root: Path,
    kb: str = typer.Option(..., "--kb"),
    index: str = typer.Option(..., "--index"),
    workers: int = typer.Option(4, "--workers", min=1, max=32),
    replace: bool = typer.Option(
        False,
        "--replace",
        help=(
            "Before uploading each suite, delete existing docs in the index whose "
            "origin_name starts with the suite slug (other suites are untouched)."
        ),
    ),
    report_path: Path = _ReportOpt,
    output: str = _OutputOpt,
) -> None:
    """Walk a directory of *.md suites and upload every section. Writes a JSON report."""

    def go() -> None:
        console = Console()
        md_files = sorted(Path(root).rglob("*.md"))
        with (
            _client() as c,
            Progress(
                TextColumn("[bold]ingest[/bold]"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                console=console,
            ) as progress,
        ):
            task = progress.add_task("ingesting", total=len(md_files))

            def on_file(_p: str) -> None:
                progress.advance(task)

            report = ingest_directory(
                c,
                root,
                kb_id=kb,
                index_id=index,
                workers=workers,
                progress=on_file,
                replace=replace,
            )
        write_report(report, report_path)
        _print_summary(report, report_path, output=output)
        if report.total_suites_failed > 0:
            raise typer.Exit(code=2)

    _run(go)


def _print_summary(report: IngestReport, report_path: Path, *, output: str) -> None:
    summary = {
        "total_suites": report.total_suites,
        "total_suites_failed": report.total_suites_failed,
        "total_sections_uploaded": report.total_sections_uploaded,
        "token_distribution": report.token_distribution,
        "report": str(report_path),
    }
    render(summary, fmt=output)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
