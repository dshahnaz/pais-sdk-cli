"""Render helper for `pais kb show`: KB header + per-index breakdown table."""

from __future__ import annotations

import datetime as _dt
from typing import Any

from rich.console import Console
from rich.table import Table

from pais.client import PaisClient
from pais.models import Index, KnowledgeBase


def fetch(client: PaisClient, kb_uuid: str) -> tuple[KnowledgeBase, list[Index]]:
    kb = client.knowledge_bases.get(kb_uuid)
    indexes = client.indexes.list(kb_uuid).data
    return kb, indexes


def to_dict(kb: KnowledgeBase, indexes: list[Index], *, epoch: bool) -> dict[str, Any]:
    return {
        "kb": _kb_dict(kb, epoch=epoch),
        "indexes": [_index_dict(i, epoch=epoch) for i in indexes],
    }


def render_table(kb: KnowledgeBase, indexes: list[Index], *, epoch: bool) -> None:
    console = Console()
    console.print(f"[bold]KB:[/bold] {kb.name}  ([dim]{kb.id}[/dim])")
    console.print(
        f"  data_origin_type: {getattr(kb.data_origin_type, 'value', kb.data_origin_type)}"
    )
    console.print(f"  description: {kb.description or '—'}")
    console.print(f"  created: {_fmt_ts(kb.created_at, epoch=epoch)}")
    extra_updated = getattr(kb, "last_updated_at", None)
    if extra_updated:
        console.print(f"  last_updated: {_fmt_ts(extra_updated, epoch=epoch)}")
    extra_next = getattr(kb, "next_index_refresh_at", None)
    if extra_next:
        console.print(f"  next_refresh: {_fmt_ts(extra_next, epoch=epoch)}")

    if not indexes:
        console.print("\n[dim]no indexes[/dim]")
        return
    console.print(f"\n[bold]Indexes ({len(indexes)}):[/bold]")
    table = Table()
    table.add_column("alias")  # filled in by caller (not present on the model)
    table.add_column("id")
    table.add_column("name")
    table.add_column("status")
    table.add_column("documents", justify="right")
    table.add_column("chunk_size", justify="right")
    table.add_column("last_indexed_at")
    for ix in indexes:
        status_val = getattr(ix.status, "value", ix.status)
        table.add_row(
            "—",
            ix.id,
            ix.name,
            str(status_val),
            str(getattr(ix, "num_documents", "—") or "—"),
            str(ix.chunk_size),
            _fmt_ts(getattr(ix, "last_indexed_at", None), epoch=epoch),
        )
    console.print(table)


def _kb_dict(kb: KnowledgeBase, *, epoch: bool) -> dict[str, Any]:
    d = kb.model_dump(mode="json", exclude_none=True)
    if not epoch:
        if "created_at" in d:
            d["created_at"] = _fmt_ts(d["created_at"], epoch=False)
        for k in ("last_updated_at", "next_index_refresh_at"):
            if k in d:
                d[k] = _fmt_ts(d[k], epoch=False)
    return d


def _index_dict(ix: Index, *, epoch: bool) -> dict[str, Any]:
    d = ix.model_dump(mode="json", exclude_none=True)
    if not epoch:
        for k in ("created_at", "last_indexed_at"):
            if k in d:
                d[k] = _fmt_ts(d[k], epoch=False)
    return d


def _fmt_ts(value: Any, *, epoch: bool) -> str:
    if value in (None, "", 0):
        return "—"
    if epoch:
        return str(value)
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return str(value)
    if ts <= 0:
        return "—"
    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
