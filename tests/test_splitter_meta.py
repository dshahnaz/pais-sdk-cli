"""Every registered splitter declares non-empty `meta` (SplitterMeta)."""

from __future__ import annotations

import pytest

from pais.ingest.registry import SPLITTER_REGISTRY
from pais.ingest.splitters._base import SplitterMeta, meta_for


@pytest.mark.parametrize("kind", sorted(SPLITTER_REGISTRY))
def test_splitter_has_meta(kind: str) -> None:
    cls = SPLITTER_REGISTRY[kind]
    m = getattr(cls, "meta", None)
    assert isinstance(m, SplitterMeta), f"{kind} is missing `meta: ClassVar[SplitterMeta]`"


@pytest.mark.parametrize("kind", sorted(SPLITTER_REGISTRY))
def test_splitter_meta_fields_populated(kind: str) -> None:
    m = meta_for(SPLITTER_REGISTRY[kind])
    assert m.summary and m.summary != "(no summary — splitter is missing `meta`)"
    assert m.input_type and m.input_type != "(unknown)"
    assert m.algorithm and m.algorithm != "(no description provided)"
    assert m.chunk_size_unit in {"tokens", "chars", "file"}
    assert m.typical_chunk_size and m.typical_chunk_size != "(unknown)"
    assert m.example_input and m.example_input != "(none)"


def test_meta_for_falls_back_when_attr_missing() -> None:
    """A splitter class without `meta` returns the placeholder, doesn't crash."""

    class _Legacy:
        kind = "_legacy"

    m = meta_for(_Legacy)
    assert m.summary == "(no summary — splitter is missing `meta`)"


def test_summary_lengths_under_70_chars() -> None:
    """Picker rows render compactly — keep summaries short."""
    for kind in sorted(SPLITTER_REGISTRY):
        m = meta_for(SPLITTER_REGISTRY[kind])
        assert len(m.summary) <= 70, f"{kind}: summary too long ({len(m.summary)} chars)"
