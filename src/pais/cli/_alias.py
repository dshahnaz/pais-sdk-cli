"""Resolve `<kb_alias>:<index_alias>` (or UUIDs) to PAIS UUIDs.

Cache: `~/.pais/aliases.json`, keyed by profile. Invalidates on 404 from
PAIS for a previously-cached UUID; the failing call is retried once with
the freshly-resolved UUID.

UUID detection is strict 8-4-4-4-12 hex; aliases must match
`[A-Za-z][A-Za-z0-9_-]*` AND not look like a UUID. Both rules are checked
at config-load time, so an ambiguous alias is rejected before it ever
reaches the resolver.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pais.errors import PaisNotFoundError
from pais.logging import get_logger

if TYPE_CHECKING:
    from pais.cli._profile_config import ProfileConfig
    from pais.client import PaisClient

_log = get_logger("pais.alias")

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
ALIAS_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

CACHE_PATH = Path.home() / ".pais" / "aliases.json"
_lock = threading.Lock()


def is_uuid(s: str) -> bool:
    return bool(UUID_RE.match(s))


def is_alias(s: str) -> bool:
    return bool(ALIAS_RE.match(s)) and not is_uuid(s)


@dataclass
class CachedKb:
    uuid: str
    name: str


@dataclass
class CachedIndex:
    uuid: str
    kb_uuid: str
    name: str


def _load_cache() -> dict[str, Any]:
    if not CACHE_PATH.exists():
        return {}
    try:
        loaded = json.loads(CACHE_PATH.read_text())
    except Exception:
        _log.warning("pais.alias.cache_corrupt", path=str(CACHE_PATH))
        return {}
    if not isinstance(loaded, dict):
        return {}
    return dict(loaded)


def _save_cache(data: dict[str, Any]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, CACHE_PATH)


def _profile_bucket(cache: dict[str, Any], profile: str) -> dict[str, Any]:
    bucket: dict[str, Any] = cache.setdefault(profile, {})
    bucket.setdefault("kbs", {})
    bucket.setdefault("indexes", {})
    return bucket


def resolve_kb(
    client: PaisClient,
    profile: str,
    kb_ref: str,
    *,
    cfg: ProfileConfig | None = None,
) -> str:
    """Return the KB UUID for `kb_ref` (a UUID or a TOML alias).

    Disambiguation: if the ref is declared in the active profile's config,
    treat as an alias. Otherwise treat as a server-side identifier (UUID or
    whatever the deployment uses) and pass through.
    """
    if cfg is None or kb_ref not in cfg.knowledge_bases:
        # Not in config → treat as server identifier; pass through.
        return kb_ref

    with _lock:
        cache = _load_cache()
        bucket = _profile_bucket(cache, profile)
        cached = bucket["kbs"].get(kb_ref)
        if cached:
            uuid = str(cached["uuid"])
            try:
                client.knowledge_bases.get(uuid)  # confirm still exists
                return uuid
            except PaisNotFoundError:
                _log.info("pais.alias.kb_invalidated", alias=kb_ref, uuid=uuid)
                bucket["kbs"].pop(kb_ref, None)
                _save_cache(cache)

        # Need to resolve. Look up by name from the active ProfileConfig.
        target_name = _kb_target_name(cfg, kb_ref)
        all_kbs = client.knowledge_bases.list().data
        match = next((k for k in all_kbs if k.name == target_name), None)
        if match is None:
            raise LookupError(
                f"KB alias {kb_ref!r} → name {target_name!r} not found on the server. "
                f"Run `pais kb ensure` to create it, or check the alias in your config."
            )
        match_uuid: str = str(match.id)
        bucket["kbs"][kb_ref] = {"uuid": match_uuid, "name": match.name}
        _save_cache(cache)
        return match_uuid


def resolve_index(
    client: PaisClient,
    profile: str,
    kb_ref: str,
    idx_ref: str,
    *,
    cfg: ProfileConfig | None = None,
) -> tuple[str, str]:
    """Return (kb_uuid, index_uuid) for the given pair of refs (UUIDs or aliases)."""
    kb_uuid = resolve_kb(client, profile, kb_ref, cfg=cfg)
    # Index alias only counts when its parent KB is also a declared alias.
    is_aliased = (
        cfg is not None
        and kb_ref in cfg.knowledge_bases
        and any(ix.alias == idx_ref for ix in cfg.knowledge_bases[kb_ref].indexes)
    )
    if not is_aliased:
        return kb_uuid, idx_ref

    cache_key = f"{kb_ref}:{idx_ref}"
    with _lock:
        cache = _load_cache()
        bucket = _profile_bucket(cache, profile)
        cached = bucket["indexes"].get(cache_key)
        if cached and cached.get("kb_uuid") == kb_uuid:
            uuid = cached["uuid"]
            try:
                client.indexes.get(kb_uuid, uuid)
                return kb_uuid, uuid
            except PaisNotFoundError:
                _log.info("pais.alias.index_invalidated", alias=cache_key, uuid=uuid)
                bucket["indexes"].pop(cache_key, None)
                _save_cache(cache)

        target_name = _index_target_name(cfg, kb_ref, idx_ref)
        all_idx = client.indexes.list(kb_uuid).data
        match = next((i for i in all_idx if i.name == target_name), None)
        if match is None:
            raise LookupError(
                f"Index alias {cache_key!r} → name {target_name!r} not found under KB {kb_ref}. "
                f"Run `pais kb ensure` to create it."
            )
        bucket["indexes"][cache_key] = {
            "uuid": match.id,
            "kb_uuid": kb_uuid,
            "name": match.name,
        }
        _save_cache(cache)
        return kb_uuid, match.id


def _kb_target_name(cfg: ProfileConfig | None, kb_alias: str) -> str:
    if cfg is None or kb_alias not in cfg.knowledge_bases:
        raise LookupError(f"KB alias {kb_alias!r} is not declared in the active profile's config.")
    return cfg.knowledge_bases[kb_alias].name


def _index_target_name(cfg: ProfileConfig | None, kb_alias: str, idx_alias: str) -> str:
    if cfg is None or kb_alias not in cfg.knowledge_bases:
        raise LookupError(f"KB alias {kb_alias!r} is not declared in the active profile's config.")
    kb = cfg.knowledge_bases[kb_alias]
    for ix in kb.indexes:
        if ix.alias == idx_alias:
            return ix.name
    raise LookupError(f"Index alias {idx_alias!r} not declared under KB {kb_alias!r}.")


def clear_cache(alias: str | None = None, *, profile: str | None = None) -> None:
    """Invalidate one or all cached entries."""
    with _lock:
        cache = _load_cache()
        if alias is None and profile is None:
            with contextlib.suppress(FileNotFoundError):
                CACHE_PATH.unlink()
            return
        if profile and profile in cache:
            if alias is None:
                cache.pop(profile, None)
            else:
                bucket = _profile_bucket(cache, profile)
                bucket["kbs"].pop(alias, None)
                # also drop any index entries that referenced this kb_alias
                for k in list(bucket["indexes"]):
                    if k.startswith(f"{alias}:") or k == alias:
                        bucket["indexes"].pop(k, None)
        _save_cache(cache)


def list_cache() -> dict[str, Any]:
    return _load_cache()


def parse_index_ref(ref: str) -> tuple[str, str]:
    """Parse `<kb_ref>:<index_ref>` into a 2-tuple. Either side may be UUID or alias."""
    if ":" not in ref:
        raise ValueError(
            f"expected '<kb_ref>:<index_ref>', got {ref!r}. "
            "Use either two aliases ('test_suites:main') or a KB+index UUID pair."
        )
    kb_part, _, idx_part = ref.partition(":")
    if not kb_part or not idx_part:
        raise ValueError(f"both sides of {ref!r} must be non-empty")
    return kb_part, idx_part
