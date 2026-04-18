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


def dump_chat_error(
    exc: Exception,
    *,
    agent_id: str,
    prompt: str,
    profile: str | None = None,
    dest_dir: Path | None = None,
) -> Path:
    """Write a JSON record of a failed chat turn. Returns the path written."""
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
    }

    if isinstance(exc, PaisError):
        payload["status_code"] = exc.status_code
        payload["request_id"] = exc.request_id
        payload["codes"] = [d.error_code for d in exc.details if d.error_code]
        payload["details"] = [asdict(d) if is_dataclass(d) else str(d) for d in exc.details]
    else:
        payload["traceback"] = traceback.format_exception(type(exc), exc, exc.__traceback__)

    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, out_path)
    return out_path
