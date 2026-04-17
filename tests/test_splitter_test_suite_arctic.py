"""End-to-end tests for the `test_suite_arctic` splitter."""

from __future__ import annotations

from pathlib import Path

from pais.dev.token_budget import token_count
from pais.ingest import get_splitter

FIXTURE = Path(__file__).parent / "fixtures" / "test_suites" / "Access-Management.md"


def test_kind_registered() -> None:
    cls = get_splitter("test_suite_arctic")
    assert cls.kind == "test_suite_arctic"


def test_meta_declares_arctic_target() -> None:
    cls = get_splitter("test_suite_arctic")
    assert cls.meta.target_embeddings_model == "Snowflake/snowflake-arctic-embed-m-v2.0"
    assert cls.meta.suggested_index_chunk_size == 2048
    assert cls.meta.suggested_index_chunk_overlap == 256


def test_arctic_produces_same_chunks_as_bge_for_this_fixture() -> None:
    """All test cases in the fixture fit under both budgets, so outputs match."""
    bge_cls = get_splitter("test_suite_bge")
    arctic_cls = get_splitter("test_suite_arctic")
    bge = bge_cls(bge_cls.options_model())
    arctic = arctic_cls(arctic_cls.options_model())
    bge_names = [d.origin_name for d in bge.split(FIXTURE)]
    arctic_names = [d.origin_name for d in arctic.split(FIXTURE)]
    assert bge_names == arctic_names


def test_every_chunk_fits_under_arctic_budget() -> None:
    cls = get_splitter("test_suite_arctic")
    sp = cls(cls.options_model())
    for d in sp.split(FIXTURE):
        n = token_count(d.body.decode())
        assert n <= 1500, f"{d.origin_name} = {n} tokens (> 1500)"


def test_arctic_allows_larger_max_case_tokens() -> None:
    cls = get_splitter("test_suite_arctic")
    opts = cls.options_model(max_case_tokens=4000)
    assert opts.max_case_tokens == 4000


def test_group_key() -> None:
    cls = get_splitter("test_suite_arctic")
    sp = cls(cls.options_model())
    assert sp.group_key(FIXTURE) == "Access-Management__"
