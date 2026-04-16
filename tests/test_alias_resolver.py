"""Alias resolver: cache + invalidation + UUID/server-id passthrough."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pais.cli import _alias
from pais.cli._profile_config import (
    IndexDeclaration,
    KnowledgeBaseDeclaration,
    ProfileConfig,
)
from pais.client import PaisClient
from pais.models import IndexCreate, KnowledgeBaseCreate
from pais.transport.fake_transport import FakeTransport
from pais_mock.state import Store


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the cache path to a tmp file so tests don't pollute the real one."""
    monkeypatch.setattr(_alias, "CACHE_PATH", tmp_path / "aliases.json")


def _make() -> tuple[PaisClient, str, str, ProfileConfig]:
    c = PaisClient(FakeTransport(Store()))
    kb = c.knowledge_bases.create(KnowledgeBaseCreate(name="real-kb"))
    ix = c.indexes.create(kb.id, IndexCreate(name="real-ix", embeddings_model_endpoint="bge"))
    cfg = ProfileConfig(
        knowledge_bases={
            "mykb": KnowledgeBaseDeclaration(
                name="real-kb",
                indexes=[
                    IndexDeclaration(alias="main", name="real-ix", embeddings_model_endpoint="bge")
                ],
            )
        }
    )
    return c, kb.id, ix.id, cfg


def test_alias_resolves_kb() -> None:
    c, kb_id, _, cfg = _make()
    assert _alias.resolve_kb(c, "test", "mykb", cfg=cfg) == kb_id


def test_alias_resolves_index() -> None:
    c, kb_id, ix_id, cfg = _make()
    assert _alias.resolve_index(c, "test", "mykb", "main", cfg=cfg) == (kb_id, ix_id)


def test_uuid_passthrough() -> None:
    c, kb_id, ix_id, cfg = _make()
    # Server IDs (or UUIDs) not declared in cfg are passed through unchanged.
    assert _alias.resolve_kb(c, "test", kb_id, cfg=cfg) == kb_id
    assert _alias.resolve_index(c, "test", "mykb", ix_id, cfg=cfg) == (kb_id, ix_id)


def test_cache_persisted_between_calls() -> None:
    c, kb_id, _, cfg = _make()
    _alias.resolve_kb(c, "p1", "mykb", cfg=cfg)
    cache = json.loads(_alias.CACHE_PATH.read_text())
    assert cache["p1"]["kbs"]["mykb"]["uuid"] == kb_id


def test_404_invalidates_cache() -> None:
    """Pre-seed the cache with a stale UUID; resolver should drop and re-resolve."""
    c, kb_id, _, cfg = _make()
    _alias.CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _alias.CACHE_PATH.write_text(
        json.dumps(
            {"test": {"kbs": {"mykb": {"uuid": "stale-uuid", "name": "real-kb"}}, "indexes": {}}}
        )
    )
    # The stale UUID will 404 on the GET; resolver re-lists KBs and finds the real one.
    resolved = _alias.resolve_kb(c, "test", "mykb", cfg=cfg)
    assert resolved == kb_id


def test_unknown_string_passes_through_as_server_id() -> None:
    """If a ref isn't declared in cfg, treat as a server-side identifier."""
    c, _, _, cfg = _make()
    # "no-such-alias" isn't in cfg → returned unchanged so the SDK call surfaces
    # a 404 from the server (real ID-not-found error) rather than a config error.
    assert _alias.resolve_kb(c, "test", "no-such-alias", cfg=cfg) == "no-such-alias"


def test_clear_cache_wipes_everything() -> None:
    c, _, _, cfg = _make()
    _alias.resolve_kb(c, "test", "mykb", cfg=cfg)
    assert _alias.CACHE_PATH.exists()
    _alias.clear_cache()
    assert not _alias.CACHE_PATH.exists()


def test_parse_index_ref_happy() -> None:
    assert _alias.parse_index_ref("kb:ix") == ("kb", "ix")


def test_parse_index_ref_missing_colon_errors() -> None:
    with pytest.raises(ValueError, match="<kb_ref>:<index_ref>"):
        _alias.parse_index_ref("oops")


def test_parse_index_ref_empty_side_errors() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        _alias.parse_index_ref("a:")
