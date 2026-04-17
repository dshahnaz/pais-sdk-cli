"""Splitter registry: built-ins registered, kinds unique, `get_splitter` rejects unknowns."""

from __future__ import annotations

import pytest

from pais.ingest import SPLITTER_REGISTRY, get_splitter


def test_builtins_registered() -> None:
    expected = {"test_suite_bge", "test_suite_arctic"}
    assert expected.issubset(SPLITTER_REGISTRY)


def test_only_test_suite_splitters_are_registered() -> None:
    """v0.7.0 shipped with ONLY the two test-suite splitters. If you added a new
    built-in, update this test to include it."""
    assert set(SPLITTER_REGISTRY) == {"test_suite_bge", "test_suite_arctic"}


def test_kind_attr_matches_registry_key() -> None:
    for key, cls in SPLITTER_REGISTRY.items():
        assert cls.kind == key


def test_kinds_unique() -> None:
    kinds = [cls.kind for cls in SPLITTER_REGISTRY.values()]
    assert len(kinds) == len(set(kinds))


def test_get_splitter_unknown_raises() -> None:
    with pytest.raises(KeyError, match="unknown splitter kind"):
        get_splitter("not_a_real_splitter")


def test_get_splitter_rejects_removed_kinds() -> None:
    """Ensure the v0.6 kinds (`passthrough`, `text_chunks`, `markdown_headings`,
    `test_suite_md`) are gone — a stale pais.toml referencing them must fail loudly."""
    for removed in ("passthrough", "text_chunks", "markdown_headings", "test_suite_md"):
        with pytest.raises(KeyError):
            get_splitter(removed)
