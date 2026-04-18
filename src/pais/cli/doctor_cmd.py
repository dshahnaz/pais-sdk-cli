"""`pais doctor` — one-shot diagnostic probe. Runs every basic listing,
captures errors with full context, emits a single shareable markdown report.

Designed so the user can paste the output into a chat or issue and we
immediately see: version, profile, mode, reachability, which endpoints
work, which fail (with status_code + request_id + redacted response body),
and where the local logs live.
"""

from __future__ import annotations

import datetime as _dt
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from pais.cli._flags import OUTPUT_OPT
from pais.cli._output import render
from pais.config import Settings
from pais.errors import PaisError
from pais.logging import _redact_value


@dataclass
class _ProbeResult:
    name: str
    ok: bool
    detail: str
    error: str | None = None
    request_id: str | None = None
    status_code: int | None = None


@dataclass
class DoctorReport:
    version: str
    profile: str
    mode: str
    base_url: str
    verify_ssl: bool
    log_file: str | None
    timestamp: str
    probes: list[_ProbeResult] = field(default_factory=list)
    inventory: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    settings_dump: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "profile": self.profile,
            "mode": self.mode,
            "base_url": self.base_url,
            "verify_ssl": self.verify_ssl,
            "log_file": self.log_file,
            "timestamp": self.timestamp,
            "probes": [
                {
                    "name": p.name,
                    "ok": p.ok,
                    "detail": p.detail,
                    "error": p.error,
                    "request_id": p.request_id,
                    "status_code": p.status_code,
                }
                for p in self.probes
            ],
            "inventory": self.inventory,
            "settings": self.settings_dump,
        }

    def to_markdown(self) -> str:
        lines = [
            f"# pais doctor — {self.timestamp}",
            "",
            f"- **version**: {self.version}",
            f"- **profile**: {self.profile}",
            f"- **mode**: {self.mode}",
            f"- **base_url**: {self.base_url}",
            f"- **verify_ssl**: {self.verify_ssl}",
            f"- **log_file**: {self.log_file or '(none)'}",
            "",
            "> Secrets auto-redacted; review before sharing.",
            "",
            "| probe | status | detail |",
            "|---|---|---|",
        ]
        for p in self.probes:
            mark = "✓" if p.ok else "✗"
            detail = p.detail
            if p.error:
                detail += f" · {p.error}"
            if p.request_id:
                detail += f" · request_id={p.request_id}"
            lines.append(f"| {p.name} | {mark} | {_redact_value(detail)} |")

        if self.settings_dump:
            lines.append("")
            lines.append("## Settings (non-secret)")
            lines.append("")
            lines.append("| key | value |")
            lines.append("|---|---|")
            for key, value in self.settings_dump.items():
                lines.append(f"| {key} | {_redact_value(str(value))} |")

        for section, rows in self.inventory.items():
            lines.append("")
            lines.append(f"<details><summary>{section} ({len(rows)})</summary>")
            lines.append("")
            if rows:
                columns = list(rows[0].keys())
                lines.append("| " + " | ".join(columns) + " |")
                lines.append("|" + "|".join(["---"] * len(columns)) + "|")
                for row in rows:
                    cells = [_redact_value(str(row.get(c, ""))) for c in columns]
                    lines.append("| " + " | ".join(cells) + " |")
            else:
                lines.append("_(none)_")
            lines.append("")
            lines.append("</details>")

        return "\n".join(lines) + "\n"


def doctor(output: str = OUTPUT_OPT) -> None:
    """Run a diagnostic probe battery and emit a shareable report."""
    from pais import __version__

    console = Console()
    settings = Settings()
    now = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H-%M-%S")

    report = DoctorReport(
        version=__version__,
        profile=settings.profile or "default",
        mode=settings.mode,
        base_url=settings.base_url,
        verify_ssl=settings.verify_ssl,
        log_file=str(settings.log_file) if settings.log_file else None,
        timestamp=now,
    )

    report.settings_dump = _dump_settings(settings)

    # --- probes ---
    with settings.build_client() as client:
        # 1. Reachability (HEAD on base URL — same as status_cmd)
        _probe(report, "server_reachable", lambda: _head_probe(settings))

        # 2. knowledge_bases list
        def _kb_probe() -> str:
            kbs = client.knowledge_bases.list().data
            report.inventory["knowledge_bases"] = [
                {
                    "id": kb.id,
                    "name": getattr(kb, "name", None),
                    "created_at": getattr(kb, "created_at", None),
                }
                for kb in kbs
            ]
            return f"list returned {len(kbs)}"

        _probe(report, "knowledge_bases", _kb_probe)

        # 3. indexes (per KB)
        def _indexes_probe() -> str:
            kbs = client.knowledge_bases.list().data
            rows: list[dict[str, Any]] = []
            for kb in kbs:
                for ix in client.indexes.list(kb.id).data:
                    rows.append(
                        {
                            "id": ix.id,
                            "kb_id": kb.id,
                            "kb_name": getattr(kb, "name", None),
                            "name": getattr(ix, "name", None),
                            "embeddings_model": getattr(ix, "embeddings_model", None),
                            "chunk_size": getattr(ix, "chunk_size", None),
                            "chunk_overlap": getattr(ix, "chunk_overlap", None),
                            "status": getattr(ix, "status", None),
                        }
                    )
            report.inventory["indexes"] = rows
            return f"all {len(kbs)} KBs scanned; {len(rows)} indexes"

        _probe(report, "indexes", _indexes_probe)

        # 4. agents
        def _agents_probe() -> str:
            agents = client.agents.list().data
            report.inventory["agents"] = [
                {
                    "id": a.id,
                    "name": getattr(a, "name", None),
                    "model": getattr(a, "model", None),
                    "index_id": getattr(a, "index_id", None),
                    "index_top_n": getattr(a, "index_top_n", None),
                    "session_max_length": getattr(a, "session_max_length", None),
                }
                for a in agents
            ]
            return f"list returned {len(agents)}"

        _probe(report, "agents", _agents_probe)

        # 5. models
        def _models_probe() -> str:
            models = client.models.list().data
            report.inventory["models"] = [
                {
                    "id": m.id,
                    "model_type": getattr(m, "model_type", None),
                    "model_engine": getattr(m, "model_engine", None),
                }
                for m in models
            ]
            return f"list returned {len(models)}"

        _probe(report, "models", _models_probe)

        # 6. mcp_tools
        _probe(
            report,
            "mcp_tools",
            lambda: f"list returned {len(client.mcp_tools.list().data)}",
        )

    # 7. alias cache
    from pais.cli._alias import CACHE_PATH, list_cache

    cache = list_cache()
    profile_bucket = cache.get(report.profile, {})
    report.probes.append(
        _ProbeResult(
            name="alias_cache",
            ok=True,
            detail=(
                f"{CACHE_PATH} ({len(profile_bucket.get('kbs', {}))} KBs, "
                f"{len(profile_bucket.get('indexes', {}))} indexes)"
            ),
        )
    )

    # 8. log file
    if settings.log_file:
        lp = Path(settings.log_file).expanduser()
        if lp.exists():
            size = lp.stat().st_size
            report.probes.append(
                _ProbeResult(
                    name="log_file",
                    ok=True,
                    detail=f"{lp} ({size} bytes)",
                )
            )
        else:
            report.probes.append(
                _ProbeResult(name="log_file", ok=True, detail=f"{lp} (not yet created)")
            )

    # --- output ---
    if output == "table":
        _render_table(report, console)
    elif output == "json":
        render(report.to_dict(), fmt="json")
    else:
        render(report.to_dict(), fmt=output)

    # --- write file ---
    log_dir = (
        Path(settings.log_file).expanduser().parent
        if settings.log_file
        else Path.home() / ".pais" / "logs"
    )
    log_dir.mkdir(parents=True, exist_ok=True)
    report_file = log_dir / f"doctor-{now}.md"
    report_file.write_text(report.to_markdown(), encoding="utf-8")
    console.print(f"\n[dim]Report written to {report_file}[/dim]")
    console.print("[dim]Paste this file into the chat (secrets are auto-redacted).[/dim]")

    if any(not p.ok for p in report.probes):
        raise typer.Exit(code=1)


def _probe(report: DoctorReport, name: str, fn: Callable[[], str]) -> None:
    """Run one probe; append result to report."""
    try:
        detail = fn()
        report.probes.append(_ProbeResult(name=name, ok=True, detail=detail))
    except PaisError as e:
        report.probes.append(
            _ProbeResult(
                name=name,
                ok=False,
                detail="failed",
                error=str(_redact_value(str(e))),
                request_id=getattr(e, "request_id", None),
                status_code=getattr(e, "status_code", None),
            )
        )
    except Exception as e:
        report.probes.append(
            _ProbeResult(
                name=name,
                ok=False,
                detail="failed",
                error=f"{type(e).__name__}: {_redact_value(str(e))}",
            )
        )


_SAFE_SETTING_KEYS = (
    "mode",
    "base_url",
    "auth",
    "verify_ssl",
    "connect_timeout",
    "read_timeout",
    "total_timeout",
    "retry_max_attempts",
    "retry_base_delay",
    "retry_max_delay",
    "chat_cold_start_retries",
    "chat_cold_start_delay",
    "chat_retry_on_empty",
    "log_level",
    "log_json_console",
    "profile",
)


def _dump_settings(settings: Settings) -> dict[str, Any]:
    """Dump only the allowlisted non-secret settings so a new SecretStr
    field added later cannot accidentally leak into shared reports."""
    out: dict[str, Any] = {}
    for key in _SAFE_SETTING_KEYS:
        if hasattr(settings, key):
            value = getattr(settings, key)
            out[key] = str(value) if value is not None else None
    return out


def _head_probe(settings: Settings) -> str:
    import httpx

    started = time.perf_counter()
    base = settings.base_url.rstrip("/")
    with httpx.Client(verify=settings.verify_ssl, timeout=5.0) as client:
        resp = client.head(base)
    ms = int((time.perf_counter() - started) * 1000)
    return f"HEAD {base} → {resp.status_code} ({ms} ms)"


def _render_table(report: DoctorReport, console: Console) -> None:
    console.print(f"[bold]pais doctor[/bold]  ·  v{report.version}  ·  {report.timestamp}\n")
    for p in report.probes:
        mark = "[green]✓[/green]" if p.ok else "[red]✗[/red]"
        line = f"  {mark} {p.name:20s} {p.detail}"
        if p.error:
            line += f"\n{'':24s}[red]{p.error}[/red]"
        if p.request_id:
            line += f"\n{'':24s}[dim]request_id={p.request_id}[/dim]"
        console.print(line)
