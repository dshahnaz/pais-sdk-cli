"""End-to-end ingest tests against the in-process mock."""

from __future__ import annotations

import json
from pathlib import Path

from pais.client import PaisClient
from pais.dev.ingest import ingest_directory, ingest_file, write_report
from pais.models import (
    AgentCreate,
    ChatCompletionRequest,
    ChatMessage,
    IndexCreate,
    KnowledgeBaseCreate,
    ToolLink,
    ToolLinkType,
)
from pais.transport.fake_transport import FakeTransport
from pais_mock.state import Store

_SUITE_MD = """# {name}

## Overview

This suite validates {topic}.

## Test Coverage

### test_{topic}_create

Creates a {topic} and validates it persists.

### test_{topic}_delete

Deletes a {topic} and confirms it is gone.

## Technology Stack

- pytest
- requests
"""


def _provision(client: PaisClient) -> tuple[str, str]:
    kb = client.knowledge_bases.create(KnowledgeBaseCreate(name="tc"))
    ix = client.indexes.create(
        kb.id,
        IndexCreate(name="ix", embeddings_model_endpoint="BAAI/bge-small-en-v1.5"),
    )
    return kb.id, ix.id


def test_ingest_file_splits_and_uploads(tmp_path: Path) -> None:
    client = PaisClient(FakeTransport(Store()))
    kb_id, ix_id = _provision(client)

    p = tmp_path / "Role-Management.md"
    p.write_text(_SUITE_MD.format(name="Role-Management", topic="role"))

    result = ingest_file(client, p, kb_id=kb_id, index_id=ix_id)
    assert result.suite_name == "Role-Management"
    assert result.sections_emitted == result.sections_uploaded
    assert result.sections_emitted >= 3  # overview + 2 tests + tech_stack
    assert not result.errors
    docs = client.indexes.list_documents(kb_id, ix_id).data
    names = {d.origin_name for d in docs}
    assert any("Role-Management__10_test__test_role_create" in n for n in names)
    client.close()


def test_ingest_directory_writes_report(tmp_path: Path) -> None:
    client = PaisClient(FakeTransport(Store()))
    kb_id, ix_id = _provision(client)
    suites_dir = tmp_path / "suites"
    suites_dir.mkdir()
    for name in ("Alpha", "Beta", "Gamma"):
        (suites_dir / f"{name}-Management.md").write_text(
            _SUITE_MD.format(name=f"{name}-Management", topic=name.lower())
        )

    report = ingest_directory(client, suites_dir, kb_id=kb_id, index_id=ix_id, workers=2)
    assert report.total_suites == 3
    assert report.total_suites_failed == 0
    assert report.total_sections_uploaded >= 9
    assert set(report.token_distribution.keys()) == {"min", "p50", "p95", "max", "budget"}

    report_path = tmp_path / "report.json"
    write_report(report, report_path)
    parsed = json.loads(report_path.read_text())
    assert parsed["summary"]["total_suites"] == 3
    assert parsed["summary"]["total_suites_failed"] == 0
    assert len(parsed["suites"]) == 3
    client.close()


def test_retrieval_returns_breadcrumb_text(tmp_path: Path) -> None:
    """End-to-end RAG: ingest → create agent → chat → answer references the right section."""
    client = PaisClient(FakeTransport(Store()))
    kb_id, ix_id = _provision(client)

    # Use a topic with a distinctive token so search can discriminate.
    p = tmp_path / "Provisioning.md"
    p.write_text(_SUITE_MD.format(name="Provisioning", topic="resource"))
    ingest_file(client, p, kb_id=kb_id, index_id=ix_id)

    tool = client.mcp_tools.find_kb_search_tool()
    assert tool is not None
    agent = client.agents.create(
        AgentCreate(
            name="a",
            model="openai/gpt-oss-120b-4x",
            tools=[
                ToolLink(
                    link_type=ToolLinkType.PAIS_KNOWLEDGE_BASE_INDEX_SEARCH_TOOL_LINK,
                    tool_id=tool.id,
                )
            ],
        )
    )
    resp = client.agents.chat(
        agent.id,
        ChatCompletionRequest(
            messages=[ChatMessage(role="user", content="how is a resource created")]
        ),
    )
    body = resp.choices[0].message.content or ""
    refs = resp.references or []
    ref_text = " ".join(r.get("text", "") for r in refs if isinstance(r, dict))
    combined = body + " " + ref_text
    # The mock agent echoes retrieved chunks verbatim — breadcrumb must appear.
    assert "Suite: Provisioning" in combined
    client.close()


def test_ingest_directory_with_replace_flag(tmp_path: Path) -> None:
    """--replace deletes only matching prior docs, leaves other suites alone."""
    client = PaisClient(FakeTransport(Store()))
    kb_id, ix_id = _provision(client)

    # First ingest: two suites.
    suites = tmp_path / "suites"
    suites.mkdir()
    (suites / "Alpha.md").write_text(_SUITE_MD.format(name="Alpha-Suite", topic="alpha"))
    (suites / "Beta.md").write_text(_SUITE_MD.format(name="Beta-Suite", topic="beta"))
    ingest_directory(client, suites, kb_id=kb_id, index_id=ix_id, workers=2)
    initial = {d.origin_name for d in client.indexes.list_documents(kb_id, ix_id).data}
    assert any("Alpha-Suite__" in n for n in initial)
    assert any("Beta-Suite__" in n for n in initial)

    # Now re-ingest Alpha only with --replace; Beta docs must remain untouched.
    only_alpha = tmp_path / "alpha-only"
    only_alpha.mkdir()
    (only_alpha / "Alpha.md").write_text(_SUITE_MD.format(name="Alpha-Suite", topic="alpha"))
    ingest_directory(client, only_alpha, kb_id=kb_id, index_id=ix_id, workers=1, replace=True)

    after = {d.origin_name for d in client.indexes.list_documents(kb_id, ix_id).data}
    # Alpha sections still present (re-uploaded; same names).
    assert any("Alpha-Suite__" in n for n in after)
    # Beta sections untouched.
    assert {n for n in after if "Beta-Suite__" in n} == {n for n in initial if "Beta-Suite__" in n}
    # No duplicates of Alpha (would be 2x original count).
    alpha_count = sum(1 for n in after if "Alpha-Suite__" in n)
    initial_alpha_count = sum(1 for n in initial if "Alpha-Suite__" in n)
    assert alpha_count == initial_alpha_count
    client.close()


def test_error_per_suite_does_not_stop_run(tmp_path: Path, monkeypatch) -> None:
    """If one suite's file is malformed, others still ingest and the run completes."""
    client = PaisClient(FakeTransport(Store()))
    kb_id, ix_id = _provision(client)
    suites_dir = tmp_path / "suites"
    suites_dir.mkdir()
    (suites_dir / "Good.md").write_text(_SUITE_MD.format(name="Good-Suite", topic="good"))
    # Bad file: empty → no H1 → splitter raises ValueError
    (suites_dir / "Bad.md").write_text("no heading here")

    report = ingest_directory(client, suites_dir, kb_id=kb_id, index_id=ix_id, workers=2)
    assert report.total_suites == 2
    assert report.total_suites_failed == 1
    by_file = {Path(s.file).name: s for s in report.suites}
    assert by_file["Good.md"].errors == []
    assert by_file["Bad.md"].errors
    client.close()
