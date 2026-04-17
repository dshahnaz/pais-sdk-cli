"""`pais splitters new <kind>` — scaffold a new splitter file + test stub + docs row.

Interactive prompts collect the SplitterMeta fields. Output:
  - src/pais/ingest/splitters/<kind>.py           (splitter skeleton w/ TODO markers)
  - tests/test_splitter_<kind>.py                 (registration + meta test stub)
  - updates src/pais/ingest/splitters/__init__.py (adds the import)
  - appends a row to docs/ingestion.md            (table + scaffolder docs)

Use `--dry-run` to preview without writing.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import typer

scaffold_app = typer.Typer(add_completion=False)

_KIND_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_VALID_UNITS = ("tokens", "chars", "file")


@dataclass
class ScaffoldInput:
    kind: str
    summary: str
    input_type: str
    example_input: str
    chunk_size_unit: str
    target_embeddings_model: str | None
    suggested_index_chunk_size: int | None
    suggested_index_chunk_overlap: int | None


def scaffold_splitter(
    kind: str = typer.Argument(..., help="Splitter kind (snake_case). Becomes the registry key."),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print what would be written without touching the filesystem."
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompts for file overwrites (dangerous — destroys existing files).",
    ),
    repo_root: Path | None = typer.Option(
        None,
        "--repo-root",
        help="Path to the pais-sdk-cli repo root. Defaults to cwd; useful when running outside the repo.",
    ),
) -> None:
    """Scaffold a new splitter: file + test + __init__ registration + doc row."""
    if not _KIND_RE.match(kind):
        raise typer.BadParameter(
            f"kind {kind!r} must be snake_case: start with a letter, "
            "then letters/digits/underscores only"
        )

    root = (repo_root or Path.cwd()).resolve()
    splitter_path = root / "src" / "pais" / "ingest" / "splitters" / f"{kind}.py"
    test_path = root / "tests" / f"test_splitter_{kind}.py"
    init_path = root / "src" / "pais" / "ingest" / "splitters" / "__init__.py"
    docs_path = root / "docs" / "ingestion.md"

    if not init_path.exists():
        raise typer.BadParameter(
            f"couldn't find splitter registry at {init_path}. "
            "Run from the repo root or pass --repo-root."
        )

    for p in (splitter_path, test_path):
        if p.exists() and not yes and not dry_run:
            raise typer.BadParameter(
                f"{p} already exists. Pass --yes to overwrite, or pick a different kind."
            )

    interactive = sys.stdin.isatty() and not dry_run
    inputs = _prompt_for_inputs(kind, interactive=interactive)
    splitter_src = _render_splitter(inputs)
    test_src = _render_test(inputs)

    if dry_run:
        typer.echo(f"--dry-run: would write {splitter_path}")
        typer.echo("--- begin splitter source ---")
        typer.echo(splitter_src)
        typer.echo("--- end splitter source ---")
        typer.echo(f"\n--dry-run: would write {test_path}")
        typer.echo("--- begin test source ---")
        typer.echo(test_src)
        typer.echo("--- end test source ---")
        typer.echo(f"\n--dry-run: would update {init_path} to import {kind}")
        if docs_path.exists():
            typer.echo(f"--dry-run: would append a row to {docs_path}")
        return

    splitter_path.parent.mkdir(parents=True, exist_ok=True)
    splitter_path.write_text(splitter_src, encoding="utf-8")
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(test_src, encoding="utf-8")
    _update_init(init_path, kind)
    if docs_path.exists():
        _append_docs_row(docs_path, inputs)

    typer.echo(f"wrote {splitter_path}")
    typer.echo(f"wrote {test_path}")
    typer.echo(f"updated {init_path}")
    if docs_path.exists():
        typer.echo(f"appended table row to {docs_path}")
    typer.echo(
        "\nNext: implement split() in the new file, run `uv run pytest tests/test_splitter_"
        + kind
        + ".py`."
    )


def _prompt_for_inputs(kind: str, *, interactive: bool) -> ScaffoldInput:
    def ask_required(label: str, default: str = "") -> str:
        if not interactive:
            return default or f"TODO({label})"
        v = typer.prompt(label, default=default) if default else typer.prompt(label)
        return str(v)

    def ask_optional(label: str, default: str = "") -> str:
        """Returns '' when user leaves blank (or we're non-interactive with no default)."""
        if not interactive:
            return default
        v = typer.prompt(label + " (blank to skip)", default=default, show_default=bool(default))
        return str(v)

    def ask_int(label: str, default: int | None) -> int | None:
        if not interactive:
            return default
        raw = typer.prompt(
            label + " (blank to skip)",
            default="" if default is None else str(default),
            show_default=default is not None,
        )
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError:
            typer.echo(f"must be an integer; got {raw!r}", err=True)
            return ask_int(label, default)

    summary = ask_required("One-line summary (≤ 70 chars)")
    if len(summary) > 70:
        typer.echo(
            f"warning: summary is {len(summary)} chars (> 70); picker rows may wrap", err=True
        )
    input_type = ask_required("Input type (e.g. 'structured markdown test suites')")
    example_input = ask_required(
        "Example input path (e.g. '~/Downloads/foo.md')", default="~/example.md"
    )
    unit = ask_required(f"Chunk size unit {_VALID_UNITS}", default="tokens")
    if unit not in _VALID_UNITS:
        typer.echo(f"must be one of {_VALID_UNITS}; got {unit!r}", err=True)
        raise typer.Exit(code=1)
    target_model = ask_optional("Target embeddings model")
    chunk_size = ask_int("Suggested index chunk_size (tokens)", default=None)
    chunk_overlap = ask_int("Suggested index chunk_overlap (tokens)", default=None)

    return ScaffoldInput(
        kind=kind,
        summary=summary,
        input_type=input_type,
        example_input=example_input,
        chunk_size_unit=unit,
        target_embeddings_model=target_model or None,
        suggested_index_chunk_size=chunk_size,
        suggested_index_chunk_overlap=chunk_overlap,
    )


def _class_name(kind: str) -> str:
    return "".join(p.capitalize() for p in kind.split("_"))


def _render_splitter(inp: ScaffoldInput) -> str:
    cls = _class_name(inp.kind)
    meta_extras: list[str] = []
    if inp.target_embeddings_model:
        meta_extras.append(f'        target_embeddings_model="{inp.target_embeddings_model}",')
    if inp.suggested_index_chunk_size is not None:
        meta_extras.append(f"        suggested_index_chunk_size={inp.suggested_index_chunk_size},")
    if inp.suggested_index_chunk_overlap is not None:
        meta_extras.append(
            f"        suggested_index_chunk_overlap={inp.suggested_index_chunk_overlap},"
        )
    extras_block = ("\n" + "\n".join(meta_extras)) if meta_extras else ""
    token_char_hint = (
        '"TODO: e.g. ≈ 4 chars/token (English)"' if inp.chunk_size_unit == "tokens" else "None"
    )
    return f'''"""`{inp.kind}` — {inp.summary}."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from pydantic import Field

from pais.ingest.registry import register_splitter
from pais.ingest.splitters._base import SplitDoc, SplitterMeta, SplitterOptionsBase


class {cls}Options(SplitterOptionsBase):
    """Options for the `{inp.kind}` splitter (declared in the TOML `[splitter]` block)."""

    # TODO: add options specific to this splitter.
    example_option: int = Field(
        default=0,
        description="TODO: describe what this option controls.",
    )


@register_splitter
class {cls}Splitter:
    """TODO: one-sentence description of what this splitter emits."""

    kind: ClassVar[str] = "{inp.kind}"
    options_model: ClassVar[type[{cls}Options]] = {cls}Options
    meta: ClassVar[SplitterMeta] = SplitterMeta(
        summary="{inp.summary}",
        input_type="{inp.input_type}",
        algorithm=(
            "TODO: 1-3 sentences in plain English describing how this splitter "
            "decides where to cut and what each emitted chunk contains."
        ),
        chunk_size_unit="{inp.chunk_size_unit}",
        typical_chunk_size="TODO: e.g. '≈ 400 tokens (~1.5 KB English)'",
        token_char_hint={token_char_hint},
        example_input="{inp.example_input}",
        notes=(),{extras_block}
    )

    def __init__(self, options: {cls}Options) -> None:
        self._options = options

    def group_key(self, path: Path) -> str:
        # TODO: return a stable prefix shared by every origin_name this splitter
        # emits for `path`. --replace uses startswith(group_key) to find prior
        # uploads that should be deleted before re-upload.
        return path.stem + "__"

    def split(self, path: Path) -> Iterator[SplitDoc]:
        # TODO: implement. Read `path`, compute chunks, yield SplitDoc per chunk.
        # Every origin_name must start with group_key(path).
        raise NotImplementedError("{inp.kind}.split() is not implemented yet")
'''


def _render_test(inp: ScaffoldInput) -> str:
    cls = _class_name(inp.kind)
    return f'''"""Tests for the `{inp.kind}` splitter. Auto-scaffolded — fill in test_split_behavior."""

from __future__ import annotations

from pais.ingest import SPLITTER_REGISTRY, get_splitter
from pais.ingest.splitters._base import SplitterMeta


def test_{inp.kind}_is_registered() -> None:
    assert "{inp.kind}" in SPLITTER_REGISTRY


def test_{inp.kind}_meta_is_populated() -> None:
    cls = get_splitter("{inp.kind}")
    meta = cls.meta
    assert isinstance(meta, SplitterMeta)
    assert meta.summary
    assert meta.algorithm and "TODO" not in meta.algorithm, (
        "replace the algorithm TODO in {inp.kind}.py once split() is implemented"
    )


def test_{inp.kind}_class_name() -> None:
    cls = get_splitter("{inp.kind}")
    assert cls.__name__ == "{cls}Splitter"


# TODO: add a test_{inp.kind}_split_behavior that exercises .split() on a fixture.
'''


_INIT_IMPORT_ANCHOR = "from pais.ingest.splitters import test_suite_arctic, test_suite_bge"


def _update_init(init_path: Path, kind: str) -> None:
    """Insert `from pais.ingest.splitters import <kind>` into the existing __init__.py.

    We add the new kind to the single comma-separated import line, keeping the
    list alphabetized. Also extends `__all__` in order.
    """
    src = init_path.read_text(encoding="utf-8")
    # Find the existing import line (the canonical line lists all built-ins).
    m = re.search(r"from pais\.ingest\.splitters import (.+)", src)
    if not m:
        raise RuntimeError(f"couldn't find the splitter import line in {init_path}")
    current = [s.strip() for s in m.group(1).split(",")]
    if kind in current:
        return  # idempotent
    new = sorted({*current, kind})
    new_import_line = f"from pais.ingest.splitters import {', '.join(new)}"
    src = re.sub(r"from pais\.ingest\.splitters import .+", new_import_line, src, count=1)
    # Update __all__ if present.
    src = re.sub(
        r"__all__\s*=\s*\[([^\]]*)\]",
        lambda _m: f"__all__ = [{', '.join(repr(k) for k in new)}]",
        src,
        count=1,
    )
    init_path.write_text(src, encoding="utf-8")


_DOCS_MARKER = "<!-- splitters-table-end -->"


def _append_docs_row(docs_path: Path, inp: ScaffoldInput) -> None:
    """Append a markdown table row if the docs have the splitters table marker.

    No-op (with a warning logged on stdout) if the marker isn't present —
    the user can edit docs manually.
    """
    src = docs_path.read_text(encoding="utf-8")
    if _DOCS_MARKER not in src:
        typer.echo(
            f"note: {docs_path} has no `{_DOCS_MARKER}` marker; skipping doc update. "
            "Edit the table manually to add a row for this splitter."
        )
        return
    row = (
        f"| `{inp.kind}` | {inp.summary} | "
        f"{inp.target_embeddings_model or '—'} | "
        f"{inp.suggested_index_chunk_size or '—'} | "
        f"{inp.suggested_index_chunk_overlap or '—'} |\n"
    )
    src = src.replace(_DOCS_MARKER, row + _DOCS_MARKER)
    docs_path.write_text(src, encoding="utf-8")
