"""Run a splitter against a real file (dry-run) and report what it WOULD emit.

The killer observability feature: the user can see exactly how many chunks
their input produces, the size distribution in BOTH tokens and chars, and
the actual chars/token ratio for their content — *before* uploading anything.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pais.ingest.registry import get_splitter
from pais.ingest.splitters._base import SplitDoc, meta_for

_DEFAULT_LIMIT = 100
_DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB
_SAMPLE_CHARS = 300


@dataclass
class _Stats:
    """min / median / max — produced from a list of ints."""

    min: int = 0
    median: int = 0
    max: int = 0
    count: int = 0

    @classmethod
    def from_values(cls, values: list[int]) -> _Stats:
        if not values:
            return cls()
        return cls(
            min=min(values),
            median=int(statistics.median(values)),
            max=max(values),
            count=len(values),
        )


@dataclass
class PreviewReport:
    """What `preview()` returns. Render with `render_panel`/`to_dict`."""

    kind: str
    path: str
    files_scanned: int
    chunks_emitted: int
    char_stats: _Stats
    token_stats: _Stats | None  # None when `tokenizers` is not installed
    char_token_ratio_median: float | None
    sample_chunk_first300: str
    truncated: bool  # True if we hit --limit or --max-bytes
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "path": self.path,
            "files_scanned": self.files_scanned,
            "chunks_emitted": self.chunks_emitted,
            "char_stats": _stats_dict(self.char_stats),
            "token_stats": _stats_dict(self.token_stats) if self.token_stats else None,
            "char_token_ratio_median": self.char_token_ratio_median,
            "sample_chunk": self.sample_chunk_first300,
            "truncated": self.truncated,
            "notes": list(self.notes),
        }


def _stats_dict(s: _Stats | None) -> dict[str, int] | None:
    if s is None:
        return None
    return {"min": s.min, "median": s.median, "max": s.max, "count": s.count}


def preview(
    kind: str,
    path: Path,
    *,
    limit: int = _DEFAULT_LIMIT,
    max_bytes: int = _DEFAULT_MAX_BYTES,
) -> PreviewReport:
    """Run `kind` over `path` (dry-run). Returns a `PreviewReport`."""
    cls = get_splitter(kind)  # raises if kind isn't registered
    splitter = cls(cls.options_model())  # all defaults

    files = _gather_files(path, limit=limit, max_bytes=max_bytes)
    truncated = len(files) >= limit
    notes: list[str] = []
    if truncated:
        notes.append(f"hit --limit {limit}; some files skipped")

    chunks: list[SplitDoc] = []
    for f in files:
        try:
            chunks.extend(splitter.split(f))
        except Exception as e:  # one bad file shouldn't kill the preview
            notes.append(f"{f.name}: {type(e).__name__} — {e}")

    char_lens = [len(c.body.decode("utf-8", errors="replace")) for c in chunks]
    char_stats = _Stats.from_values(char_lens)

    token_stats: _Stats | None = None
    ratio_median: float | None = None
    try:
        from pais.dev.token_budget import token_count

        token_lens = [token_count(c.body.decode("utf-8", errors="replace")) for c in chunks]
        token_stats = _Stats.from_values(token_lens)
        ratios = [(cl / tl) for cl, tl in zip(char_lens, token_lens, strict=False) if tl > 0]
        ratio_median = round(statistics.median(ratios), 2) if ratios else None
    except ImportError:
        notes.append("token counts unavailable — install `pais-sdk-cli[dev]` (or `tokenizers`)")

    sample = ""
    if chunks:
        sample = chunks[0].body.decode("utf-8", errors="replace")[:_SAMPLE_CHARS]

    return PreviewReport(
        kind=kind,
        path=str(path),
        files_scanned=len(files),
        chunks_emitted=len(chunks),
        char_stats=char_stats,
        token_stats=token_stats,
        char_token_ratio_median=ratio_median,
        sample_chunk_first300=sample,
        truncated=truncated,
        notes=notes,
    )


def _gather_files(path: Path, *, limit: int, max_bytes: int) -> list[Path]:
    if path.is_file():
        return [path]
    out: list[Path] = []
    total = 0
    for p in sorted(path.rglob("*")):
        if not p.is_file():
            continue
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        if total + sz > max_bytes:
            break
        total += sz
        out.append(p)
        if len(out) >= limit:
            break
    return out


def render_panel(report: PreviewReport, console: Console) -> None:
    """Pretty-print a PreviewReport (table mode)."""
    cls_meta = meta_for(get_splitter(report.kind))
    header = (
        f"[bold cyan]{report.kind}[/bold cyan]  ·  [dim]{cls_meta.summary}[/dim]\n"
        f"preview against [bold]{report.path}[/bold]"
    )
    console.print(Panel.fit(header, border_style="cyan"))

    summary = Table(show_header=False, box=None, pad_edge=False)
    summary.add_column(style="bold cyan", no_wrap=True)
    summary.add_column()
    summary.add_row("Files scanned", str(report.files_scanned))
    summary.add_row("Chunks emitted", str(report.chunks_emitted))
    if report.token_stats:
        ts = report.token_stats
        summary.add_row(
            "Token distribution",
            f"min={ts.min}  median={ts.median}  max={ts.max}   [dim](BAAI/bge-small-en-v1.5)[/dim]",
        )
    cs = report.char_stats
    summary.add_row(
        "Char distribution",
        f"min={cs.min}  median={cs.median}  max={cs.max}",
    )
    if report.char_token_ratio_median is not None:
        summary.add_row(
            "Ratio (chars/token)",
            f"median={report.char_token_ratio_median}",
        )
    console.print(summary)

    if report.sample_chunk_first300:
        console.print(
            Panel(
                report.sample_chunk_first300,
                title="Sample (first 300 chars of chunk #1)",
                border_style="dim",
            )
        )

    for n in report.notes:
        console.print(f"[yellow]· {n}[/yellow]")
    console.print("[dim](no upload happened — this was a dry-run)[/dim]")
