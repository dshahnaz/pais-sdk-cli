"""Tests for `pais.cli._error_dump.dump_chat_error`."""

from __future__ import annotations

import json
from pathlib import Path

from pais.cli._error_dump import dump_chat_error
from pais.errors import ErrorDetail, PaisServerError


def test_dump_chat_error_pais_error_payload(tmp_path: Path) -> None:
    """PaisError → status_code, request_id, codes, details captured."""
    exc = PaisServerError(
        "PAIS request failed with status 502",
        status_code=502,
        request_id="req-xyz-1",
        details=[
            ErrorDetail(error_code="AGENT_COMPLETION_FAILED", msg="Parsing inference failed"),
        ],
    )
    path = dump_chat_error(
        exc,
        agent_id="agent-1",
        prompt="hello world",
        profile="prod",
        dest_dir=tmp_path,
    )
    assert path.parent == tmp_path
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status_code"] == 502
    assert data["request_id"] == "req-xyz-1"
    assert data["codes"] == ["AGENT_COMPLETION_FAILED"]
    assert data["agent_id"] == "agent-1"
    assert data["profile"] == "prod"
    assert data["error_type"] == "PaisServerError"
    assert data["prompt_excerpt"] == "hello world"
    assert data["prompt_bytes"] == len(b"hello world")
    assert data["prompt_truncated"] is False
    assert data["details"][0]["error_code"] == "AGENT_COMPLETION_FAILED"


def test_dump_chat_error_truncates_long_prompt(tmp_path: Path) -> None:
    """Prompts over the 2000-char cap are truncated and flagged."""
    long = "a" * 5000
    path = dump_chat_error(
        ValueError("nope"),
        agent_id="agent-2",
        prompt=long,
        dest_dir=tmp_path,
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["prompt_truncated"] is True
    assert len(data["prompt_excerpt"]) == 2000
    assert data["prompt_bytes"] == 5000


def test_dump_chat_error_generic_exception_has_traceback(tmp_path: Path) -> None:
    """Non-PaisError carries a traceback; PaisError-only fields are absent."""
    try:
        raise RuntimeError("boom")
    except RuntimeError as e:
        path = dump_chat_error(e, agent_id="a3", prompt="p", dest_dir=tmp_path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["error_type"] == "RuntimeError"
    assert "traceback" in data
    assert any("RuntimeError: boom" in line for line in data["traceback"])
    assert "status_code" not in data
    assert "codes" not in data


def test_dump_chat_error_filename_shape(tmp_path: Path) -> None:
    """Filename is <UTC-ts>-<request_id>.json; directory is created with mode 0o700."""
    exc = PaisServerError("x", status_code=500, request_id="rid-42")
    path = dump_chat_error(exc, agent_id="a", prompt="p", dest_dir=tmp_path / "errs")
    assert path.name.endswith("-rid-42.json")
    assert path.parent.name == "errs"
    assert path.parent.exists()


def test_dump_chat_error_no_request_id_uses_placeholder(tmp_path: Path) -> None:
    """If the exception has no request_id, filename uses a stable placeholder."""
    path = dump_chat_error(ValueError("no id"), agent_id="a", prompt="p", dest_dir=tmp_path)
    assert path.name.endswith("-no-request-id.json")
