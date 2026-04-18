"""`pais repro` — reproducible chat-experiment harness.

Build a fresh KB+index+agent from supplied fixtures (test-suites dir,
instructions md), run a list of prompts against it, capture per-prompt
metrics (prompt_tokens, completion_tokens, finish_reason, latency, full
response or error), and bundle everything into a single zip suitable for
hand-off / regression archival.

Inputs are flags only — no interactive steps. Same recipe shipped in
the bundle so re-runs are bit-identical.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import typer

from pais.cli._error_dump import dump_chat_error
from pais.cli._flags import HELP_OPTION_NAMES
from pais.client import PaisClient
from pais.config import Settings
from pais.errors import PaisError
from pais.ingest.registry import get_splitter
from pais.ingest.runner import ingest_path
from pais.models import (
    AgentCreate,
    ChatCompletionRequest,
    ChatMessage,
    IndexCreate,
    KnowledgeBaseCreate,
)

_LOG_DIR = Path.home() / ".pais" / "logs"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _ts() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@contextmanager
def _capture_response_log() -> Any:
    """Capture the most recent `pais.response.chat` log record for the wrapped call.

    The transport already emits this at INFO level (v0.7.7); we patch
    `_log.info` on the transport module to also stash the kwargs.
    """
    from pais.transport import httpx_transport as ht

    captured: dict[str, Any] = {}
    real_info = ht._log.info

    def spy(event: str, **kw: Any) -> None:
        if event == "pais.response.chat":
            captured.clear()
            captured.update(kw)
        real_info(event, **kw)

    ht._log.info = spy  # type: ignore[method-assign,assignment]
    try:
        yield captured
    finally:
        ht._log.info = real_info  # type: ignore[method-assign]


def _run_one_prompt(
    client: PaisClient,
    *,
    agent_id: str,
    prompt_path: Path,
    max_tokens: int | None,
) -> dict[str, Any]:
    """Send one prompt to the agent and capture metrics. Never raises."""
    content = prompt_path.read_text(encoding="utf-8")
    record: dict[str, Any] = {
        "prompt_file": prompt_path.name,
        "prompt_path": str(prompt_path),
        "prompt_bytes": len(content.encode("utf-8")),
        "prompt_sha256": _sha256_text(content),
        "agent_id": agent_id,
        "max_tokens_requested": max_tokens,
    }
    started = time.perf_counter()
    try:
        with _capture_response_log() as captured:
            req = ChatCompletionRequest(
                messages=[ChatMessage(role="user", content=content)],
                max_tokens=max_tokens,
            )
            resp = client.agents.chat(agent_id, req)
        latency_ms = int((time.perf_counter() - started) * 1000)
        choice = resp.choices[0]
        record.update(
            {
                "ok": True,
                "latency_ms": latency_ms,
                "request_id": captured.get("request_id"),
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
                "finish_reason": choice.finish_reason,
                "response_text": choice.message.content or "",
                "response_bytes": len((choice.message.content or "").encode("utf-8")),
            }
        )
    except Exception as e:
        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            dump_path = dump_chat_error(e, agent_id=agent_id, prompt=content)
        except Exception:
            dump_path = None
        record.update(
            {
                "ok": False,
                "latency_ms": latency_ms,
                "error_type": type(e).__name__,
                "error_message": str(e),
                "status_code": getattr(e, "status_code", None),
                "request_id": getattr(e, "request_id", None),
                "codes": (
                    [d.error_code for d in e.details if d.error_code]
                    if isinstance(e, PaisError)
                    else None
                ),
                "chat_error_dump": str(dump_path) if dump_path else None,
            }
        )
    return record


def repro(
    suites_dir: Path = typer.Option(
        ...,
        "--suites-dir",
        exists=True,
        file_okay=False,
        dir_okay=True,
        help="Directory of test-suite markdown files to ingest into a fresh KB.",
    ),
    instructions: Path = typer.Option(
        ...,
        "--instructions",
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Markdown file whose contents become the agent's system instructions.",
    ),
    prompts: list[Path] = typer.Option(
        ...,
        "--prompts",
        exists=True,
        file_okay=True,
        dir_okay=False,
        help="Prompt file(s) to send. Repeat the flag to add more.",
    ),
    model: str = typer.Option("openai/gpt-oss-120b", "--model", help="Agent's chat model."),
    embeddings_model: str = typer.Option(
        "BAAI/bge-small-en-v1.5", "--embeddings-model", help="Embeddings model for the index."
    ),
    splitter: str = typer.Option("test_suite_bge", "--splitter", help="Splitter kind to use."),
    chunk_size: int = typer.Option(400, "--chunk-size", help="Server-side index chunk_size."),
    chunk_overlap: int = typer.Option(80, "--chunk-overlap", help="Server-side index overlap."),
    index_top_n: int = typer.Option(5, "--index-top-n", help="Agent retrieval top_n."),
    max_tokens: int | None = typer.Option(
        None, "--max-tokens", help="Per-request max_tokens cap. Omit for server default."
    ),
    cleanup: bool = typer.Option(
        False,
        "--cleanup",
        help="Delete the created KB + agent after the run (cascades indexes).",
    ),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Bundle zip path (default: /tmp/pais-repro-<ts>.zip)."
    ),
    include_instructions: bool = typer.Option(
        False,
        "--include-instructions",
        help="Include the instructions markdown verbatim in the bundle (off by default).",
    ),
) -> None:
    """Stand up KB+index+agent, run prompts, bundle everything for triage."""
    ts = _ts()
    out_path = (output or Path("/tmp") / f"pais-repro-{ts}.zip").expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    settings = Settings()
    instr_text = instructions.read_text(encoding="utf-8")
    manifest: dict[str, Any] = {
        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "pais_version": __import__("pais").__version__,
        "profile": settings.profile,
        "mode": settings.mode,
        "base_url": settings.base_url,
        "recipe": {
            "suites_dir": str(suites_dir),
            "instructions": str(instructions),
            "instructions_bytes": len(instr_text.encode("utf-8")),
            "instructions_sha256": _sha256_text(instr_text),
            "prompts": [str(p) for p in prompts],
            "model": model,
            "embeddings_model": embeddings_model,
            "splitter": splitter,
            "chunk_size": chunk_size,
            "chunk_overlap": chunk_overlap,
            "index_top_n": index_top_n,
            "max_tokens": max_tokens,
            "cleanup": cleanup,
        },
    }

    typer.echo(f"↻ creating KB + index + agent (mode={settings.mode}) …", err=True)
    with PaisClient.from_settings(settings) as client:
        kb_name = f"repro-{ts}"
        kb = client.knowledge_bases.create(KnowledgeBaseCreate(name=kb_name))
        manifest["kb_id"] = kb.id
        manifest["kb_name"] = kb.name

        ix = client.indexes.create(
            kb.id,
            IndexCreate(
                name=f"repro-{ts}-idx",
                embeddings_model_endpoint=embeddings_model,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            ),
        )
        manifest["index_id"] = ix.id

        # Ingest the suites dir.
        typer.echo(f"↻ ingesting {suites_dir} via splitter={splitter} …", err=True)
        splitter_cls = get_splitter(splitter)
        splitter_inst = splitter_cls(splitter_cls.options_model())
        ingest_report = ingest_path(
            client,
            suites_dir,
            splitter=splitter_inst,
            kb_id=kb.id,
            index_id=ix.id,
            workers=4,
        )
        manifest["ingest_summary"] = {
            "total_files": ingest_report.total_files,
            "total_chunks_uploaded": ingest_report.total_chunks_uploaded,
            "total_failed": ingest_report.total_failed,
            "chunk_size_distribution": ingest_report.chunk_size_distribution,
        }

        # Create agent.
        agent = client.agents.create(
            AgentCreate(
                name=f"repro-{ts}-agent",
                model=model,
                instructions=instr_text,
                index_id=ix.id,
                index_top_n=index_top_n,
            )
        )
        manifest["agent_id"] = agent.id

        # Run prompts.
        typer.echo(f"↻ running {len(prompts)} prompt(s) …", err=True)
        responses: list[dict[str, Any]] = []
        for p in prompts:
            typer.echo(f"  · {p.name}", err=True)
            rec = _run_one_prompt(client, agent_id=agent.id, prompt_path=p, max_tokens=max_tokens)
            responses.append(rec)

        manifest["responses_summary"] = [
            {
                "prompt_file": r["prompt_file"],
                "ok": r.get("ok"),
                "prompt_tokens": r.get("prompt_tokens"),
                "completion_tokens": r.get("completion_tokens"),
                "finish_reason": r.get("finish_reason"),
                "status_code": r.get("status_code"),
                "latency_ms": r.get("latency_ms"),
            }
            for r in responses
        ]

        # Optional cleanup.
        if cleanup:
            typer.echo(f"↻ cleaning up agent {agent.id} + KB {kb.id} …", err=True)
            try:
                client.agents.delete(agent.id)
            except Exception as e:
                manifest["cleanup_agent_error"] = f"{type(e).__name__}: {e}"
            try:
                client.knowledge_bases.delete(kb.id)
            except Exception as e:
                manifest["cleanup_kb_error"] = f"{type(e).__name__}: {e}"

    # Generate doctor snapshot (best effort — survives doctor failures).
    typer.echo("↻ running pais doctor for the bundle …", err=True)
    import contextlib as _cl

    from pais.cli.doctor_cmd import doctor as _doctor_fn

    with _cl.suppress(typer.Exit, Exception):
        _doctor_fn(output="json")
    doctor_reports = sorted(_LOG_DIR.glob("doctor-*.md"))
    latest_doctor = doctor_reports[-1] if doctor_reports else None

    # Assemble the zip.
    chat_errors_dir = _LOG_DIR / "chat-errors"
    chat_errors = sorted(chat_errors_dir.glob("*.json")) if chat_errors_dir.exists() else []
    pais_log = _LOG_DIR / "pais.log"

    typer.echo(f"↻ bundling → {out_path}", err=True)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))
        for rec in responses:
            arcname = f"responses/{rec['prompt_file']}.json"
            z.writestr(arcname, json.dumps(rec, indent=2, default=str))
        if latest_doctor is not None:
            z.write(latest_doctor, arcname="doctor.md")
        if include_instructions:
            z.writestr("instructions.md", instr_text)
        for ce in chat_errors:
            z.write(ce, arcname=f"chat-errors/{ce.name}")
        if pais_log.exists():
            z.write(pais_log, arcname="pais.log")

    typer.echo(
        json.dumps(
            {
                "bundle": str(out_path),
                "kb_id": manifest.get("kb_id"),
                "index_id": manifest.get("index_id"),
                "agent_id": manifest.get("agent_id"),
                "responses": len(responses),
                "ok_count": sum(1 for r in responses if r.get("ok")),
                "fail_count": sum(1 for r in responses if not r.get("ok")),
            }
        )
    )


app = typer.Typer(add_completion=False, context_settings=HELP_OPTION_NAMES)
app.command(name="repro")(repro)
