"""Splitter registry: built-ins registered, options validate, kinds unique."""

from __future__ import annotations

from pathlib import Path

import pytest

from pais.ingest import SPLITTER_REGISTRY, get_splitter


def test_builtins_registered() -> None:
    expected = {"test_suite_md", "passthrough", "markdown_headings", "text_chunks"}
    assert expected.issubset(SPLITTER_REGISTRY)


def test_kind_attr_matches_registry_key() -> None:
    for key, cls in SPLITTER_REGISTRY.items():
        assert cls.kind == key


def test_kinds_unique() -> None:
    kinds = [cls.kind for cls in SPLITTER_REGISTRY.values()]
    assert len(kinds) == len(set(kinds))


def test_get_splitter_unknown_raises() -> None:
    with pytest.raises(KeyError, match="unknown splitter kind"):
        get_splitter("not_a_real_splitter")


def test_passthrough_yields_one_doc(tmp_path: Path) -> None:
    cls = get_splitter("passthrough")
    sp = cls(cls.options_model())
    p = tmp_path / "data.bin"
    p.write_bytes(b"\x00\x01\x02hello")
    docs = list(sp.split(p))
    assert len(docs) == 1
    assert docs[0].body == b"\x00\x01\x02hello"
    assert docs[0].origin_name == "data.bin"
    # group_key is the full filename so prefix-match is exact for --replace.
    assert sp.group_key(p) == "data.bin"


def test_text_chunks_chunk_count_and_overlap(tmp_path: Path) -> None:
    cls = get_splitter("text_chunks")
    sp = cls(cls.options_model(chunk_chars=100, overlap_chars=20))
    p = tmp_path / "log.txt"
    p.write_text("x" * 1000)
    docs = list(sp.split(p))
    # step = 100 - 20 = 80; chunks until idx >= 1000 → ceil(1000/80) = 13
    assert len(docs) == 13
    assert all(len(d.body) <= 100 for d in docs)
    assert sp.group_key(p) == "log__"


def test_text_chunks_overlap_must_be_smaller_than_chunk(tmp_path: Path) -> None:
    cls = get_splitter("text_chunks")
    with pytest.raises(ValueError, match="overlap_chars must be smaller"):
        cls(cls.options_model(chunk_chars=100, overlap_chars=100))


def test_markdown_headings_h2_split(tmp_path: Path) -> None:
    cls = get_splitter("markdown_headings")
    sp = cls(cls.options_model(heading_level=2, breadcrumb=True))
    md = "# My Doc\n\n## A\n\nA body.\n\n## B\n\nB body.\n"
    p = tmp_path / "doc.md"
    p.write_text(md)
    docs = list(sp.split(p))
    assert len(docs) == 2
    assert docs[0].origin_name.startswith("My_Doc__")
    assert b"# Doc: My Doc" in docs[0].body
    assert b"## Section: A" in docs[0].body
    assert sp.group_key(p) == "My_Doc__"


def test_markdown_headings_no_breadcrumb(tmp_path: Path) -> None:
    cls = get_splitter("markdown_headings")
    sp = cls(cls.options_model(heading_level=2, breadcrumb=False))
    p = tmp_path / "x.md"
    p.write_text("# T\n## A\nbody\n")
    docs = list(sp.split(p))
    assert b"# Doc:" not in docs[0].body
