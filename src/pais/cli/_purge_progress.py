"""Rich progress widget for `pais kb purge` / `pais index purge`.

The SDK's `Indexes.purge(..., on_progress=cb)` / `KnowledgeBases.purge(...,
on_progress=cb)` emit six event types. This helper packages them into one
consistent terminal view — a spinner + counter + current index name — for
the human-facing CLI paths, while remaining a no-op for `--output json`
/ non-TTY consumers who don't want ANSI escape codes in their logs.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)


def _should_render(output: str) -> bool:
    """True only when the user asked for table output AND stdout is a TTY.

    JSON/YAML consumers pipe to scripts or disk; a Rich bar would insert
    carriage returns + escape codes into their captured stream. Non-TTY
    table output (e.g. `pais kb purge foo | tee log`) falls back to plain
    echo lines for the same reason."""
    return output == "table" and sys.stdout.isatty()


@contextmanager
def purge_progress(*, output: str, console: Console | None = None):  # type: ignore[no-untyped-def]
    """Yield an `on_progress(event, **payload)` callback wired to a Rich
    progress bar (if `output == "table"` on a TTY) or to no-op (otherwise).

    Example:
        with purge_progress(output=output) as on_progress:
            c.indexes.purge(kb, ix, on_progress=on_progress)
    """
    if not _should_render(output):
        # No-op callback — SDK still walks the events; we just drop them.
        yield _noop
        return

    con = console or Console()
    progress = Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}"),
        BarColumn(bar_width=None),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TextColumn("{task.fields[index_label]}"),
        TimeElapsedColumn(),
        console=con,
        transient=False,
    )

    with progress:
        task_id = progress.add_task(
            "purging",
            total=None,  # indeterminate until we see "collected"
            index_label="",
        )
        state = {"current_index": ""}

        def on_progress(event: str, **payload: Any) -> None:
            if event == "index_start":
                # KB purge emits this per-index. Reset counters for the new index.
                i = payload.get("i")
                n = payload.get("n")
                name = payload.get("index_name") or payload.get("index_id") or ""
                state["current_index"] = name
                label = f"· [dim]{name}[/dim]"
                if i is not None and n is not None:
                    label = f"· [dim]({i}/{n}) {name}[/dim]"
                progress.reset(task_id, total=None, index_label=label)
            elif event == "collected":
                total = int(payload.get("total", 0))
                progress.update(task_id, total=total or None, completed=0)
            elif event == "deleted":
                progress.update(task_id, completed=int(payload.get("deleted", 0)))
            elif event == "index_done":
                # For KB purge only. Print a one-liner so the user sees which
                # indexes completed even after the bar moves on.
                name = state["current_index"] or payload.get("index_id", "?")
                progress.console.print(
                    f"  [green]✓[/green] {name}: {payload.get('deleted', 0)} docs"
                )
            elif event == "error":
                # Log the individual error under the bar without stopping.
                progress.console.print(
                    f"  [red]✗[/red] {payload.get('doc_id', '?')}: {payload.get('error', '')}"
                )
            elif event == "done":
                # Final tick — no-op, the context manager will clean up.
                pass

        yield on_progress


def _noop(_event: str, **_payload: Any) -> None:
    """Placeholder callback for non-TTY / JSON output paths."""
    return None


# Re-export the type for callers that want to annotate their own wiring.
ProgressCallback = Callable[..., None]
