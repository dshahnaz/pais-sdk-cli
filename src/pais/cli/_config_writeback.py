"""Safe append-only writes to `pais.toml` — workflows use these to record
newly-created KBs / indexes / splitter configs as aliases.

Design choices (driven by the safety review):
- **Append-only**: never rewrites or reorders the existing file. New blocks
  go at the very end, after a `\\n# --- added by pais workflows ---\\n`
  marker. Comments and unknown sections above the marker are preserved
  byte-for-byte.
- **Idempotent**: if a block for `(profile, alias)` already exists, the
  writer skips and returns "already present".
- **Diff preview before write**: the caller renders a unified diff with
  `preview_diff(...)` and confirms before calling `commit(...)`.
- **Refuse on parse error**: if the existing file doesn't parse as TOML, we
  do nothing and surface the error so the user can fix it manually.
"""

from __future__ import annotations

import difflib
import sys
from pathlib import Path
from typing import Any

import tomli_w

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

_MARKER = "# --- added by pais workflows ---"


class WritebackError(RuntimeError):
    """Raised when the existing file can't be parsed or the path is invalid."""


def append_kb_block(
    *,
    config_path: Path,
    profile: str,
    alias: str,
    kb_name: str,
    description: str | None = None,
    data_origin_type: str = "DATA_SOURCES",
) -> str:
    """Build a TOML block for a new KB. Returns the block as a string ready
    to append (does NOT write — call `commit_append` after diff confirm)."""
    payload: dict[str, Any] = {
        "profiles": {
            profile: {
                "knowledge_bases": {
                    alias: {
                        "name": kb_name,
                        **({"description": description} if description else {}),
                        "data_origin_type": data_origin_type,
                    }
                }
            }
        }
    }
    return tomli_w.dumps(payload)


def append_index_block(
    *,
    profile: str,
    kb_alias: str,
    idx_alias: str,
    name: str,
    embeddings_model_endpoint: str,
    text_splitting: str = "SENTENCE",
    chunk_size: int = 512,
    chunk_overlap: int = 64,
    description: str | None = None,
    splitter_kind: str | None = None,
    splitter_options: dict[str, Any] | None = None,
) -> str:
    """Build the `[[profiles.X.knowledge_bases.Y.indexes]]` block (with
    optional `[…indexes.splitter]` sub-table)."""
    idx: dict[str, Any] = {
        "alias": idx_alias,
        "name": name,
        "embeddings_model_endpoint": embeddings_model_endpoint,
        "text_splitting": text_splitting,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
    }
    if description:
        idx["description"] = description
    if splitter_kind:
        idx["splitter"] = {"kind": splitter_kind, **(splitter_options or {})}
    payload = {"profiles": {profile: {"knowledge_bases": {kb_alias: {"indexes": [idx]}}}}}
    return tomli_w.dumps(payload)


def block_exists(config_path: Path, profile: str, kb_alias: str) -> bool:
    """Return True if `[profiles.<profile>.knowledge_bases.<kb_alias>]` is
    already declared (either as an existing entry or in our own append block).
    Used by callers to skip idempotently."""
    if not config_path.exists():
        return False
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise WritebackError(f"{config_path}: invalid TOML: {e}") from e
    return kb_alias in raw.get("profiles", {}).get(profile, {}).get("knowledge_bases", {})


def index_exists(config_path: Path, profile: str, kb_alias: str, idx_alias: str) -> bool:
    if not config_path.exists():
        return False
    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise WritebackError(f"{config_path}: invalid TOML: {e}") from e
    kb = raw.get("profiles", {}).get(profile, {}).get("knowledge_bases", {}).get(kb_alias, {})
    return any(ix.get("alias") == idx_alias for ix in kb.get("indexes", []))


def preview_diff(config_path: Path, *blocks: str) -> str:
    """Render a unified diff of what `commit_append(blocks)` would produce."""
    before = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    after = _compose_after(before, blocks)
    diff = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=str(config_path),
        tofile=str(config_path) + " (after)",
    )
    return "".join(diff)


def commit_append(config_path: Path, *blocks: str) -> None:
    """Append `blocks` to `config_path` (creating it if missing). Refuses
    if the existing file fails to parse as TOML."""
    before = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    if before.strip():
        try:
            tomllib.loads(before)
        except tomllib.TOMLDecodeError as e:
            raise WritebackError(
                f"{config_path}: refusing to append — existing file isn't valid TOML: {e}"
            ) from e
    after = _compose_after(before, blocks)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp.write_text(after, encoding="utf-8")
    tmp.replace(config_path)


def _compose_after(before: str, blocks: tuple[str, ...]) -> str:
    """Build the post-write file content. Inserts the marker once."""
    if not blocks:
        return before
    parts: list[str] = []
    if before:
        parts.append(before.rstrip() + "\n")
    if _MARKER not in before:
        parts.append("\n" + _MARKER + "\n")
    for b in blocks:
        parts.append("\n" + b.rstrip() + "\n")
    return "".join(parts)
