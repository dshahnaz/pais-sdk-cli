"""Unit tests for the shared test-suite parsing + chunking primitives."""

from __future__ import annotations

from pathlib import Path

import pytest

from pais.ingest.splitters import _test_suite_core as core

FIXTURE = Path(__file__).parent / "fixtures" / "test_suites" / "Access-Management.md"


def _fake_token_count(s: str) -> int:
    # Deterministic & cheap — 1 token per whitespace-separated word.
    return max(1, len(s.split()))


def test_parse_markdown_extracts_suite_structure() -> None:
    suite = core.parse_markdown(FIXTURE.read_text())
    assert suite.name == "Access-Management"
    assert suite.overview
    assert suite.deployment
    assert suite.components
    assert suite.tech_stack
    case_names = [tc.name for tc in suite.test_cases]
    assert "testGetAllRoles" in case_names
    assert "testCreateUserRole" in case_names
    assert "deleteObjectScopeAgainTest" in case_names
    assert len(suite.test_cases) == 11


def test_parse_tolerates_missing_sections() -> None:
    md = "# Only-Cases\n\n## Test Coverage\n\n### testFoo\n\nBody.\n"
    suite = core.parse_markdown(md)
    assert suite.name == "Only-Cases"
    assert suite.overview == ""
    assert suite.deployment == ""
    assert [tc.name for tc in suite.test_cases] == ["testFoo"]


def test_parse_ignores_headings_inside_code_fences() -> None:
    md = "# S\n\n## Test Coverage\n\n### testA\n\n```\n## Not A Section\n```\n"
    suite = core.parse_markdown(md)
    assert len(suite.test_cases) == 1
    assert "Not A Section" in suite.test_cases[0].body


def test_extract_testbed_prefers_backtick_id() -> None:
    body = "- **Testbed Type**: Configured one node Ops deployment (`vrops-1slice-config-ph`)"
    assert core.extract_testbed(body) == "vrops-1slice-config-ph"


def test_extract_testbed_falls_back_to_full_text() -> None:
    body = "- **Testbed Type**: plain-description-no-backticks"
    assert core.extract_testbed(body) == "plain-description-no-backticks"


def test_extract_components_takes_short_name() -> None:
    body = (
        "- **Ops (vRealize Operations) - Platform core service for monitoring**\n"
        "- **VIDB (vCenter Identity Database) - Identity management**\n"
    )
    assert core.extract_components(body) == ["Ops", "VIDB"]


def test_build_breadcrumb_joins_components() -> None:
    bc = core.build_breadcrumb("My-Suite", "bench-1", ["A", "B"])
    assert bc == "# Suite: My-Suite\n# Testbed: bench-1 | Components: A, B\n"


def test_build_breadcrumb_falls_back_when_no_context() -> None:
    assert core.build_breadcrumb("S", "", []) == "# Suite: S\n"


def test_slug_preserves_hyphens() -> None:
    assert core.slug("Access-Management") == "Access-Management"
    assert core.slug("with spaces & punct!") == "with-spaces-punct"


def test_fit_to_budget_returns_original_when_under_budget() -> None:
    body = "small body"
    parts = core.fit_to_budget(body, 100, _fake_token_count)
    assert parts == [body]


def test_fit_to_budget_sub_splits_on_bold_labels() -> None:
    body = (
        "**Purpose**: foo foo foo.\n\n"
        "**Validations**: bar bar bar.\n\n"
        "**Key Operations**: baz baz baz.\n"
    )
    # Budget of 5 tokens (= 5 words) forces each sub-section into its own part.
    parts = core.fit_to_budget(body, 5, _fake_token_count)
    assert len(parts) >= 2
    for p in parts:
        assert _fake_token_count(p) <= 5


def test_render_chunk_joins_breadcrumb_and_body() -> None:
    out = core.render_chunk("# Suite: X\n", "body here")
    assert out == "# Suite: X\n\nbody here\n"


def test_emit_chunks_produces_overview_plus_cases() -> None:
    cfg = core.EmitConfig(max_chunk_tokens=10_000, emit_overview_chunk=True, context_fn=None)
    docs = list(core.emit_chunks(FIXTURE, cfg, _fake_token_count))
    assert any(d.origin_name.endswith("__overview.md") for d in docs)
    assert any("testCreateUserRole" in d.origin_name for d in docs)
    # Every origin_name starts with the suite slug prefix (group_key contract).
    for d in docs:
        assert d.origin_name.startswith("Access-Management__")


def test_emit_chunks_respects_emit_overview_false() -> None:
    cfg = core.EmitConfig(max_chunk_tokens=10_000, emit_overview_chunk=False, context_fn=None)
    docs = list(core.emit_chunks(FIXTURE, cfg, _fake_token_count))
    assert not any(d.origin_name.endswith("__overview.md") for d in docs)


def test_emit_chunks_prepends_context_sentence_when_fn_provided() -> None:
    cfg = core.EmitConfig(
        max_chunk_tokens=10_000,
        emit_overview_chunk=False,
        context_fn=lambda _body: "CTX_SENTINEL",
    )
    docs = list(core.emit_chunks(FIXTURE, cfg, _fake_token_count))
    assert all(b"> _Context_: CTX_SENTINEL" in d.body for d in docs)


@pytest.mark.parametrize(
    "source,expected",
    [
        ("# Title\n\nbody\n", "Title"),
        ("", ""),  # no H1 → empty name; splitter falls back to path.stem
    ],
)
def test_parse_markdown_name(source: str, expected: str) -> None:
    assert core.parse_markdown(source).name == expected
