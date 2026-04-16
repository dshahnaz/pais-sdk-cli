"""Per-profile LRU cache of recently-used aliases — pickers prepend the
last-3 with a ★ so returning users hit ↵ in one keystroke.

Storage: `~/.pais/recent.json`, shaped:
    {
      "lab": {
        "kbs":     ["prod_docs", "pdfs", "scratch"],
        "indexes": ["prod_docs:main", "pdfs:main"],
        "agents":  ["agent_demo"],
      },
      ...
    }

Each list is a most-recently-used-first ordering, capped at 10 entries
(LRU eviction on the 11th add). Corruption → empty cache, no crash.
"""

from __future__ import annotations

import contextlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Literal

Kind = Literal["kbs", "indexes", "agents"]

CACHE_PATH = Path.home() / ".pais" / "recent.json"
_MAX_PER_KIND = 10
_lock = threading.Lock()


def record_use(kind: Kind, alias: str, *, profile: str) -> None:
    """Mark `alias` as just-used for the given (profile, kind). Idempotent."""
    if not alias:
        return
    with _lock:
        cache = _load()
        bucket = _profile_bucket(cache, profile)
        items = bucket.setdefault(kind, [])
        # Move-to-front: drop existing then prepend, cap at _MAX_PER_KIND.
        new_items = [alias] + [x for x in items if x != alias]
        bucket[kind] = new_items[:_MAX_PER_KIND]
        _save(cache)


def recent(kind: Kind, *, profile: str, limit: int = 3) -> list[str]:
    """Return the last `limit` items for the given (profile, kind), most-recent first."""
    with _lock:
        cache = _load()
        bucket = cache.get(profile) or {}
        items = bucket.get(kind) or []
        return list(items)[:limit]


def clear(profile: str | None = None) -> None:
    """Wipe the whole cache (profile=None) or one profile's bucket."""
    with _lock:
        if profile is None:
            with contextlib.suppress(FileNotFoundError):
                CACHE_PATH.unlink()
            return
        cache = _load()
        if profile in cache:
            cache.pop(profile, None)
            _save(cache)


def _load() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {}
    try:
        loaded = json.loads(CACHE_PATH.read_text())
    except Exception:
        # Corruption → empty cache, never block a command.
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _save(data: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, CACHE_PATH)


def _profile_bucket(cache: dict[str, Any], profile: str) -> dict[str, Any]:
    bucket: dict[str, Any] = cache.setdefault(profile, {})
    bucket.setdefault("kbs", [])
    bucket.setdefault("indexes", [])
    bucket.setdefault("agents", [])
    return bucket
