"""Run a splitter against a real file (dry-run) and report what it WOULD emit.

The killer observability feature: the user can see exactly how many chunks
their input produces, the size distribution in BOTH tokens and chars, and
the actual chars/token ratio for their content — *before* uploading anything.

`--dump <dir>` writes every chunk to disk (filename = `origin_name`) so the
user can open each one and verify the breadcrumb / budget / semantic slicing
before committing to a 300-file upload. `--show-all` prints the header + first
200 chars of each chunk inline.
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
_SHOW_ALL_CHARS = 200


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
class DumpedChunk:
    """One chunk written to disk by `--dump <dir>`. Reported back for rendering."""

    origin_name: str
    path: str
    bytes: int
    tokens: int | None
    first_chars: str


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
    target_embeddings_model: str | None = None
    suggested_index_chunk_size: int | None = None
    suggested_index_chunk_overlap: int | None = None
    dumped: list[DumpedChunk] = field(default_factory=list)
    dump_dir: str | None = None
    show_all: bool = False

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
            "target_embeddings_model": self.target_embeddings_model,
            "suggested_index_chunk_size": self.suggested_index_chunk_size,
            "suggested_index_chunk_overlap": self.suggested_index_chunk_overlap,
            "dump_dir": self.dump_dir,
            "dumped": [
                {
                    "origin_name": d.origin_name,
                    "path": d.path,
                    "bytes": d.bytes,
                    "tokens": d.tokens,
                }
                for d in self.dumped
            ],
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
    dump_to: Path | None = None,
    show_all: bool = False,
) -> PreviewReport:
    """Run `kind` over `path` (dry-run). Returns a `PreviewReport`.

    `dump_to`: if set, writes every emitted chunk to that directory (filename
    = `origin_name`) so the user can open each one and verify size/breadcrumb/
    semantic slicing before committing to an upload.
    `show_all`: populates `DumpedChunk.first_chars` for every chunk so the CLI
    can print per-chunk excerpts inline (works with or without `dump_to`).
    """
    cls = get_splitter(kind)  # raises if kind isn't registered
    splitter = cls(cls.options_model())  # all defaults
    m = meta_for(cls)

    files = _gather_files(path, limit=limit, max_bytes=max_bytes)
    truncated = len(files) >= limit
    notes: list[str] = []
    if truncated:
        notes.append(f"hit --limit {limit}; some files skipped")

    if dump_to is not None:
        dump_to.mkdir(parents=True, exist_ok=True)

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
    token_lens: list[int] = []
    try:
        from pais.dev.token_budget import token_count

        token_lens = [token_count(c.body.decode("utf-8", errors="replace")) for c in chunks]
        token_stats = _Stats.from_values(token_lens)
        ratios = [(cl / tl) for cl, tl in zip(char_lens, token_lens, strict=False) if tl > 0]
        ratio_median = round(statistics.median(ratios), 2) if ratios else None
    except ImportError:
        notes.append("token counts unavailable — install `pais-sdk-cli[dev]` (or `tokenizers`)")

    dumped: list[DumpedChunk] = []
    if dump_to is not None or show_all:
        for i, c in enumerate(chunks):
            body = c.body.decode("utf-8", errors="replace")
            out_path = ""
            if dump_to is not None:
                p = dump_to / c.origin_name
                p.write_bytes(c.body)
                out_path = str(p)
            dumped.append(
                DumpedChunk(
                    origin_name=c.origin_name,
                    path=out_path,
                    bytes=len(c.body),
                    tokens=token_lens[i] if i < len(token_lens) else None,
                    first_chars=body[:_SHOW_ALL_CHARS],
                )
            )

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
        target_embeddings_model=m.target_embeddings_model,
        suggested_index_chunk_size=m.suggested_index_chunk_size,
        suggested_index_chunk_overlap=m.suggested_index_chunk_overlap,
        dumped=dumped,
        dump_dir=str(dump_to) if dump_to else None,
        show_all=show_all,
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

    if report.target_embeddings_model or report.suggested_index_chunk_size:
        rec = Table(show_header=False, box=None, pad_edge=False)
        rec.add_column(style="bold green", no_wrap=True)
        rec.add_column()
        if report.target_embeddings_model:
            rec.add_row("embeddings_model_endpoint", report.target_embeddings_model)
        if report.suggested_index_chunk_size is not None:
            rec.add_row("chunk_size", f"{report.suggested_index_chunk_size} tokens")
        if report.suggested_index_chunk_overlap is not None:
            rec.add_row("chunk_overlap", f"{report.suggested_index_chunk_overlap} tokens")
        console.print(
            Panel(rec, title="Recommended index config for this splitter", border_style="green")
        )

    if report.dumped:
        if report.dump_dir:
            console.print(f"\n[bold]Wrote {len(report.dumped)} chunks to[/bold] {report.dump_dir}")
        if report.show_all:
            for d in report.dumped:
                tok = f"{d.tokens} tok" if d.tokens is not None else f"{d.bytes} B"
                console.print(f"\n[bold cyan]{d.origin_name}[/bold cyan]  [dim]({tok})[/dim]")
                console.print(Panel(d.first_chars, border_style="dim"))
        else:
            dtable = Table(show_header=True, header_style="bold")
            dtable.add_column("#", no_wrap=True, justify="right")
            dtable.add_column("origin_name")
            dtable.add_column("tokens", justify="right")
            dtable.add_column("bytes", justify="right")
            for i, d in enumerate(report.dumped, 1):
                dtable.add_row(
                    str(i),
                    d.origin_name,
                    str(d.tokens) if d.tokens is not None else "—",
                    str(d.bytes),
                )
            console.print(dtable)
    elif report.sample_chunk_first300:
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
