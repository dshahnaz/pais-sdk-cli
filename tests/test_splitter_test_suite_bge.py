"""End-to-end tests for the `test_suite_bge` splitter against a real fixture.

Guards the three properties that matter for RAG quality:
  1. every chunk carries the suite breadcrumb (embedding captures context)
  2. every chunk fits under the 400-token budget (PAIS index won't re-split it)
  3. origin_names follow the `<suite-slug>__NN__<section>.md` convention
     (`--replace` matches exactly one suite at a time)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pais.dev.token_budget import token_count
from pais.ingest import get_splitter

FIXTURE = Path(__file__).parent / "fixtures" / "test_suites" / "Access-Management.md"


@pytest.fixture
def splitter():  # type: ignore[no-untyped-def]
    cls = get_splitter("test_suite_bge")
    return cls(cls.options_model())


def test_kind_registered() -> None:
    cls = get_splitter("test_suite_bge")
    assert cls.kind == "test_suite_bge"


def test_meta_declares_bge_target() -> None:
    cls = get_splitter("test_suite_bge")
    assert cls.meta.target_embeddings_model == "BAAI/bge-small-en-v1.5"
    assert cls.meta.suggested_index_chunk_size == 512
    assert cls.meta.suggested_index_chunk_overlap == 64


def test_emits_overview_plus_one_chunk_per_test_case(splitter) -> None:  # type: ignore[no-untyped-def]
    docs = list(splitter.split(FIXTURE))
    # Access-Management has 11 test cases + 1 overview = 12 chunks (none get sub-split under 400).
    assert len(docs) == 12
    assert docs[0].origin_name == "Access-Management__00__overview.md"
    case_files = [d.origin_name for d in docs[1:]]
    assert "Access-Management__01__testGetAllRoles.md" in case_files
    assert "Access-Management__02__testCreateUserRole.md" in case_files


def test_every_chunk_fits_under_400_tokens(splitter) -> None:  # type: ignore[no-untyped-def]
    for d in splitter.split(FIXTURE):
        n = token_count(d.body.decode())
        assert n <= 400, f"{d.origin_name} = {n} tokens (> 400)"


def test_every_chunk_starts_with_breadcrumb(splitter) -> None:  # type: ignore[no-untyped-def]
    for d in splitter.split(FIXTURE):
        text = d.body.decode()
        assert text.startswith("# Suite: Access-Management\n")
        # Testbed id should be the short backtick form, not the long descriptor.
        assert "vrops-1slice-config-ph" in text.splitlines()[1]


def test_group_key_matches_every_emitted_origin_name(splitter) -> None:  # type: ignore[no-untyped-def]
    prefix = splitter.group_key(FIXTURE)
    assert prefix == "Access-Management__"
    for d in splitter.split(FIXTURE):
        assert d.origin_name.startswith(prefix)


def test_emit_overview_false_suppresses_overview_chunk() -> None:
    cls = get_splitter("test_suite_bge")
    sp = cls(cls.options_model(emit_overview_chunk=False))
    docs = list(sp.split(FIXTURE))
    assert not any(d.origin_name.endswith("__overview.md") for d in docs)
    assert len(docs) == 11  # just the test cases


def test_metadata_on_chunks(splitter) -> None:  # type: ignore[no-untyped-def]
    docs = list(splitter.split(FIXTURE))
    assert docs[0].metadata["kind"] == "overview"
    assert docs[0].metadata["suite_name"] == "Access-Management"
    assert docs[1].metadata["kind"] == "test_case"
    assert docs[1].metadata["case_name"]  # non-empty
