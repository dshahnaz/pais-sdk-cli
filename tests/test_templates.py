"""Tests for `pais.cli._templates` and the `--template` flag on `pais agent create`."""

from __future__ import annotations

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from pais.cli._templates import (
    AGENT_TEMPLATES,
    apply_template,
    get_template,
    list_templates,
)
from pais.cli.app import app as cli_app


def test_field_proven_template_carries_5_recommended_fields() -> None:
    t = get_template("agent", "field-proven")
    assert t.defaults["completion_role"] == "assistant"
    assert t.defaults["session_max_length"] == 10000
    assert t.defaults["session_summarization_strategy"] == "delete_oldest"
    assert t.defaults["index_reference_format"] == "structured"
    assert t.defaults["chat_system_instruction_mode"] == "system-message"
    assert t.defaults["index_top_n"] == 5


def test_apply_template_explicit_overrides_win() -> None:
    """Explicit non-None overrides always beat the template seed."""
    resolved = apply_template(
        "agent",
        "field-proven",
        {"session_max_length": 99999, "completion_role": None},
    )
    assert resolved["session_max_length"] == 99999  # override won
    assert resolved["completion_role"] == "assistant"  # None override → seed kept
    assert resolved["index_reference_format"] == "structured"  # untouched seed


def test_apply_template_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown agent template"):
        apply_template("agent", "no-such-template", {})


def test_list_templates_returns_copy() -> None:
    a = list_templates("agent")
    a.clear()
    assert len(AGENT_TEMPLATES) > 0  # mutation didn't leak


@pytest.fixture
def mock_runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setenv("PAIS_MODE", "mock")
    monkeypatch.setenv("PAIS_LOG_LEVEL", "WARNING")
    return CliRunner()


def test_templates_list_command(mock_runner: CliRunner) -> None:
    """`pais templates list` prints all templates with their defaults."""
    r = mock_runner.invoke(cli_app, ["templates", "list"])
    assert r.exit_code == 0, r.output
    assert "field-proven" in r.output
    assert "test-suite-bge" in r.output
    assert "local-files-manual" in r.output
    assert "system-message" in r.output


def test_templates_list_filtered_by_kind(mock_runner: CliRunner) -> None:
    r = mock_runner.invoke(cli_app, ["templates", "list", "--kind", "agent"])
    assert r.exit_code == 0, r.output
    assert "field-proven" in r.output
    assert "test-suite-bge" not in r.output  # index template, not agent


def test_agent_create_with_template_field_proven_sends_5_fields(
    mock_runner: CliRunner,
) -> None:
    """`--template field-proven` populates the 5 recipe fields on the wire."""
    r = mock_runner.invoke(
        cli_app,
        [
            "agent",
            "create",
            "--name",
            "tpl-test",
            "--model",
            "openai/gpt-oss-120b-4x",
            "--template",
            "field-proven",
            "--index-id",
            "idx_1",
            "--output",
            "json",
        ],
    )
    assert r.exit_code == 0, r.output
    agent = json.loads(r.output)
    assert agent["name"] == "tpl-test"
    # The mock store echoes back what we sent for these 5 fields.
    assert agent["completion_role"] == "assistant"
    assert agent["session_max_length"] == 10000
    assert agent["session_summarization_strategy"] == "delete_oldest"
    assert agent["index_reference_format"] == "structured"
    assert agent["chat_system_instruction_mode"] == "system-message"


def test_agent_create_explicit_flag_beats_template(mock_runner: CliRunner) -> None:
    """Explicit --session-max-length overrides the template's 10000."""
    r = mock_runner.invoke(
        cli_app,
        [
            "agent",
            "create",
            "--name",
            "override-test",
            "--model",
            "openai/gpt-oss-120b-4x",
            "--template",
            "field-proven",
            "--session-max-length",
            "5000",
            "--index-id",
            "idx_1",
            "--output",
            "json",
        ],
    )
    assert r.exit_code == 0, r.output
    agent: dict[str, Any] = json.loads(r.output)
    assert agent["session_max_length"] == 5000  # explicit beat template
    assert agent["completion_role"] == "assistant"  # other template fields intact


def test_agent_create_unknown_template_errors(mock_runner: CliRunner) -> None:
    r = mock_runner.invoke(
        cli_app,
        [
            "agent",
            "create",
            "--name",
            "x",
            "--model",
            "openai/gpt-oss-120b-4x",
            "--template",
            "nonexistent-template",
        ],
    )
    assert r.exit_code != 0
