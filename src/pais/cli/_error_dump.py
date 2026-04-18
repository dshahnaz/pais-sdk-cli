"""Persist chat failures as shareable JSON blobs under `~/.pais/logs/chat-errors/`.

Each file captures the full `PaisError` context (status, request_id, codes,
detail list) plus a truncated prompt excerpt so infra can reproduce server-side
failures without the user having to retype the error."""

from __future__ import annotations

import json
import os
import traceback
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pais import __version__
from pais.errors import PaisError

_CHAT_ERRORS_DIR = Path.home() / ".pais" / "logs" / "chat-errors"
_PROMPT_EXCERPT_MAX = 2000


def _estimate_tokens(text: str) -> int:
    """Char-based token estimate. Approximate; use only when a real tokenizer is not available."""
    return max(len(text) // 4, 1)


def _summarize_request(
    *,
    model: str | None,
    max_tokens: int | None,
    temperature: float | None,
    top_p: float | None,
    stream: bool | None,
    message_count: int,
    total_chars: int,
) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        "stream": stream,
        "message_count": message_count,
        "total_chars": total_chars,
        "prompt_token_estimate_chars": _estimate_tokens("a" * total_chars) if total_chars else 0,
    }


def _safe_agent_dump(client: Any, agent_id: str) -> dict[str, Any] | None:
    """Best-effort `pais agent get`. Truncates `instructions` for the dump."""
    if client is None:
        return None
    try:
        agent = client.agents.get(agent_id)
        d: dict[str, Any] = agent.model_dump(mode="json", exclude_none=False)
        if isinstance(d.get("instructions"), str) and len(d["instructions"]) > _PROMPT_EXCERPT_MAX:
            d["instructions_chars"] = len(d["instructions"])
            d["instructions"] = d["instructions"][:_PROMPT_EXCERPT_MAX]
            d["instructions_truncated"] = True
        return d
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def dump_chat_error(
    exc: Exception,
    *,
    agent_id: str,
    prompt: str,
    profile: str | None = None,
    dest_dir: Path | None = None,
    request_summary: dict[str, Any] | None = None,
    client: Any = None,
) -> Path:
    """Write a JSON record of a failed chat turn. Returns the path written.

    Optional `request_summary` (built via `_summarize_request`) and `client`
    (a `PaisClient`) enrich the dump with the actual request body and an
    inline agent_dump. Both are best-effort — failures inside this function
    never hide the original exception.
    """
    errors_dir = dest_dir or _CHAT_ERRORS_DIR
    errors_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    request_id = getattr(exc, "request_id", None) or "no-request-id"
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = errors_dir / f"{ts}-{request_id}.json"

    prompt_bytes = len(prompt.encode("utf-8"))
    prompt_excerpt = prompt[:_PROMPT_EXCERPT_MAX]
    prompt_truncated = len(prompt) > _PROMPT_EXCERPT_MAX

    payload: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pais_version": __version__,
        "profile": profile,
        "agent_id": agent_id,
        "error_type": type(exc).__name__,
        "error_message": str(exc),
        "prompt_bytes": prompt_bytes,
        "prompt_excerpt": prompt_excerpt,
        "prompt_truncated": prompt_truncated,
        "prompt_token_estimate": _estimate_tokens(prompt),
    }

    if request_summary is not None:
        payload["request_body"] = request_summary

    if isinstance(exc, PaisError):
        payload["status_code"] = exc.status_code
        payload["request_id"] = exc.request_id
        payload["codes"] = [d.error_code for d in exc.details if d.error_code]
        payload["details"] = [asdict(d) if is_dataclass(d) else str(d) for d in exc.details]
        if exc.response_headers:
            payload["response_headers"] = exc.response_headers
    else:
        payload["traceback"] = traceback.format_exception(type(exc), exc, exc.__traceback__)

    agent_dump = _safe_agent_dump(client, agent_id)
    if agent_dump is not None:
        payload["agent_dump"] = agent_dump

    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, out_path)
    return out_path
