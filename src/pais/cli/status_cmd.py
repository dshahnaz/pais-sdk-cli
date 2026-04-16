"""`pais status` — one-shot environment overview.

Sections rendered:
  * Profile + connection (mode, base_url, auth, verify_ssl)
  * Server reachability (`GET /health` ping, skipped with `--no-ping`)
  * Alias cache state (path, count, age)
  * Knowledge bases (alias → name → indexes/docs counts → updated)
  * Indexes (alias → name → status → docs → embeddings)
  * Drift vs. TOML (read-only; same diff as `pais kb ensure --dry-run`)

Output formats: `table` (rich), `json`, `yaml`. JSON emits one machine-readable
payload covering every section so it's safe shell glue.
"""

from __future__ import annotations

import datetime as _dt
import time
from collections.abc import Callable
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.table import Table

from pais.cli import _alias
from pais.cli._config_file import load_profile_config
from pais.cli._flags import EPOCH_OPT, OUTPUT_OPT, WITH_COUNTS_OPT
from pais.cli._output import exit_code_for, render
from pais.cli.ensure_cmd import EnsureReport, _ensure_for_profile
from pais.client import PaisClient
from pais.config import Settings
from pais.errors import PaisError


def status(
    with_counts: bool = WITH_COUNTS_OPT,
    epoch: bool = EPOCH_OPT,
    no_ping: bool = typer.Option(False, "--no-ping", help="Skip the server health check."),
    output: str = OUTPUT_OPT,
) -> None:
    """Full env overview: profile, server, KBs, indexes, drift-vs-TOML."""

    def go() -> None:
        settings = Settings()
        cfg, cfg_path, profile = load_profile_config()

        payload: dict[str, Any] = {
            "profile": _profile_section(settings, cfg_path, profile),
            "server": _server_section(settings, no_ping=no_ping),
            "alias_cache": _alias_cache_section(profile),
        }

        with settings.build_client() as c:
            kbs_section = _kbs_section(c, cfg, with_counts=with_counts, epoch=epoch)
            payload["knowledge_bases"] = kbs_section["kbs"]
            payload["indexes"] = kbs_section["indexes"]
            payload["drift"] = _drift_section(c, cfg, profile)

        if output == "table":
            _render_table(payload, with_counts=with_counts)
        else:
            render(payload, fmt=output)

    _run(go)


# ----- sections ---------------------------------------------------------------


def _profile_section(settings: Settings, cfg_path: Any, profile: str) -> dict[str, Any]:
    return {
        "name": profile,
        "config_file": str(cfg_path) if cfg_path else None,
        "mode": settings.mode,
        "base_url": settings.base_url,
        "auth": settings.auth,
        "verify_ssl": settings.verify_ssl,
    }


def _server_section(settings: Settings, *, no_ping: bool) -> dict[str, Any]:
    if settings.mode == "mock":
        return {"reachable": True, "mode": "mock", "skipped": True, "latency_ms": None}
    if no_ping:
        return {"reachable": None, "skipped": True, "latency_ms": None}

    base = settings.base_url.rstrip("/")
    # PAIS doesn't document a /health endpoint; HEAD on the base URL is the
    # most portable way to detect "server answers TCP+TLS at all".
    started = time.perf_counter()
    try:
        with httpx.Client(verify=settings.verify_ssl, timeout=5.0) as client:
            resp = client.head(base)
        latency_ms = int((time.perf_counter() - started) * 1000)
        return {
            "reachable": True,
            "status_code": resp.status_code,
            "latency_ms": latency_ms,
            "skipped": False,
        }
    except Exception as e:
        return {
            "reachable": False,
            "error": str(e),
            "latency_ms": None,
            "skipped": False,
        }


def _alias_cache_section(profile: str) -> dict[str, Any]:
    cache_path = _alias.CACHE_PATH
    if not cache_path.exists():
        return {
            "path": str(cache_path),
            "exists": False,
            "kbs": 0,
            "indexes": 0,
            "age_seconds": None,
        }
    cache = _alias.list_cache()
    bucket = cache.get(profile, {})
    age_seconds: float | None
    try:
        age_seconds = max(0.0, time.time() - cache_path.stat().st_mtime)
    except OSError:
        age_seconds = None
    return {
        "path": str(cache_path),
        "exists": True,
        "kbs": len(bucket.get("kbs", {}) or {}),
        "indexes": len(bucket.get("indexes", {}) or {}),
        "age_seconds": age_seconds,
    }


def _kbs_section(
    client: PaisClient,
    cfg: Any,
    *,
    with_counts: bool,
    epoch: bool,
) -> dict[str, list[dict[str, Any]]]:
    """Build the KB + index rows together so we can fill in alias columns."""
    server_kbs = client.knowledge_bases.list().data
    name_to_alias = {decl.name: alias for alias, decl in cfg.knowledge_bases.items()}
    kb_rows: list[dict[str, Any]] = []
    index_rows: list[dict[str, Any]] = []

    for kb in server_kbs:
        kb_alias = name_to_alias.get(kb.name)
        kb_row: dict[str, Any] = {
            "alias": kb_alias or "—",
            "name": kb.name,
            "id": kb.id,
            "data_origin_type": getattr(kb.data_origin_type, "value", kb.data_origin_type),
            "updated": _fmt_ts(getattr(kb, "last_updated_at", None), epoch=epoch),
        }

        idx_alias_by_name: dict[str, str] = {}
        if kb_alias and kb_alias in cfg.knowledge_bases:
            idx_alias_by_name = {ix.name: ix.alias for ix in cfg.knowledge_bases[kb_alias].indexes}

        if with_counts:
            indexes = client.indexes.list(kb.id).data
            kb_row["indexes_count"] = len(indexes)
            kb_row["documents"] = sum(getattr(i, "num_documents", 0) or 0 for i in indexes)
            for ix in indexes:
                index_rows.append(
                    {
                        "alias": (
                            f"{kb_alias}:{idx_alias_by_name[ix.name]}"
                            if kb_alias and ix.name in idx_alias_by_name
                            else "—"
                        ),
                        "kb_alias": kb_alias or "—",
                        "name": ix.name,
                        "id": ix.id,
                        "status": getattr(ix.status, "value", ix.status),
                        "documents": getattr(ix, "num_documents", 0) or 0,
                        "embeddings_model_endpoint": ix.embeddings_model_endpoint,
                        "chunk_size": ix.chunk_size,
                    }
                )
        kb_rows.append(kb_row)

    return {"kbs": kb_rows, "indexes": index_rows}


def _drift_section(client: PaisClient, cfg: Any, profile: str) -> list[dict[str, Any]]:
    """Run the same diff as `pais kb ensure --dry-run`, but collect rows only."""
    if not cfg.knowledge_bases:
        return []
    report = EnsureReport(profile=profile, dry_run=True)
    try:
        _ensure_for_profile(client, cfg, report=report, dry_run=True, prune=False)
    except Exception as e:
        return [{"action": "error", "detail": str(e)}]
    out: list[dict[str, Any]] = []
    for r in report.rows:
        if r.action in ("existing",):
            continue
        out.append(
            {
                "kind": r.kind,
                "alias": r.alias,
                "name": r.name,
                "action": r.action,
                "detail": r.detail,
            }
        )
    return out


# ----- table rendering --------------------------------------------------------


def _render_table(payload: dict[str, Any], *, with_counts: bool) -> None:
    console = Console()

    prof = payload["profile"]
    console.print(
        f"[bold]Profile[/bold]      : {prof['name']}  ([dim]{prof['config_file'] or 'no config file'}[/dim])"
    )
    console.print(f"[bold]Mode[/bold]         : {prof['mode']}")
    console.print(f"[bold]Base URL[/bold]     : {prof['base_url']}")
    console.print(f"[bold]Auth[/bold]         : {prof['auth']}")
    console.print(f"[bold]Verify SSL[/bold]   : {prof['verify_ssl']}")

    srv = payload["server"]
    if srv.get("skipped") and srv.get("mode") == "mock":
        console.print("[bold]Server[/bold]       : [dim]mock (in-process)[/dim]")
    elif srv.get("skipped"):
        console.print("[bold]Server[/bold]       : [dim]skipped (--no-ping)[/dim]")
    elif srv.get("reachable"):
        latency = srv.get("latency_ms")
        console.print(
            f"[bold]Server[/bold]       : [green]reachable[/green]  "
            f"({latency} ms, status={srv.get('status_code')})"
        )
    else:
        console.print(
            f"[bold]Server[/bold]       : [red]unreachable[/red]  ({srv.get('error', '?')})"
        )

    cache = payload["alias_cache"]
    if cache["exists"]:
        age = cache["age_seconds"]
        age_str = _humanize_seconds(age) if age is not None else "?"
        console.print(
            f"[bold]Alias cache[/bold]  : {cache['path']}  "
            f"({cache['kbs']} KBs, {cache['indexes']} indexes, last refreshed {age_str} ago)"
        )
    else:
        console.print(
            f"[bold]Alias cache[/bold]  : {cache['path']}  [dim](not yet populated)[/dim]"
        )

    kbs = payload["knowledge_bases"]
    console.print(f"\n[bold]Knowledge bases ({len(kbs)})[/bold]")
    if kbs:
        kb_table = Table()
        for col in ("alias", "name", "id"):
            kb_table.add_column(col)
        if with_counts:
            kb_table.add_column("indexes", justify="right")
            kb_table.add_column("docs", justify="right")
        kb_table.add_column("updated")
        for row in kbs:
            cells = [row["alias"], row["name"], row["id"]]
            if with_counts:
                cells.append(str(row.get("indexes_count", "—")))
                cells.append(str(row.get("documents", "—")))
            cells.append(row["updated"])
            kb_table.add_row(*cells)
        console.print(kb_table)
    else:
        console.print("[dim](none)[/dim]")

    if with_counts:
        idxs = payload["indexes"]
        console.print(f"\n[bold]Indexes ({len(idxs)})[/bold]")
        if idxs:
            ix_table = Table()
            for col in (
                "alias",
                "name",
                "status",
                "documents",
                "embeddings_model_endpoint",
                "chunk_size",
            ):
                ix_table.add_column(col)
            for row in idxs:
                ix_table.add_row(
                    row["alias"],
                    row["name"],
                    str(row["status"]),
                    str(row["documents"]),
                    str(row["embeddings_model_endpoint"]),
                    str(row["chunk_size"]),
                )
            console.print(ix_table)
        else:
            console.print("[dim](none)[/dim]")

    drift = payload["drift"]
    console.print("\n[bold]Drift (vs. TOML)[/bold]")
    if not drift:
        console.print("[green]✓[/green] in sync")
        return
    for row in drift:
        action = row.get("action", "?")
        marker = {
            "would-create": "[yellow]+[/yellow]",
            "mismatch": "[yellow]⚠[/yellow]",
            "would-prune": "[red]-[/red]",
            "error": "[red]✗[/red]",
        }.get(action, "·")
        line = f"  {marker}  {row.get('alias', '—')}  {action}"
        detail = row.get("detail")
        if detail:
            line += f"  {detail}"
        console.print(line)


# ----- helpers ----------------------------------------------------------------


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


def _humanize_seconds(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86_400:
        return f"{s // 3600}h"
    return f"{s // 86_400}d"


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
