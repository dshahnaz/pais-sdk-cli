"""Contract round-trip tests: every fixture must parse → serialize to an equivalent structure."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pais.errors import (
    PaisAuthError,
    PaisError,
    PaisNotFoundError,
    PaisRateLimitError,
    PaisServerError,
    PaisValidationError,
    error_from_response,
)
from pais.models import (
    Agent,
    ChatCompletionResponse,
    Document,
    Index,
    Indexing,
    KnowledgeBase,
    ListResponse,
    McpTool,
    Model,
)

FIXTURES = Path(__file__).parent / "fixtures"

ROUND_TRIP_CASES = [
    ("knowledge_base.json", KnowledgeBase),
    ("index.json", Index),
    ("indexing_done.json", Indexing),
    ("document.json", Document),
    ("agent.json", Agent),
    ("chat_completion.json", ChatCompletionResponse),
    ("mcp_tool.json", McpTool),
]


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize(("fixture", "model_cls"), ROUND_TRIP_CASES)
def test_round_trip(fixture: str, model_cls: type) -> None:
    raw = _load(fixture)
    obj = model_cls.model_validate(raw)
    dumped = obj.model_dump(mode="json", exclude_none=True)
    # Every key in the original JSON must survive round-trip with equal value.
    for k, v in raw.items():
        assert k in dumped, f"Field {k!r} dropped during round-trip"
        assert dumped[k] == v, f"Field {k!r} changed: {v!r} -> {dumped[k]!r}"


def test_model_list_round_trip() -> None:
    raw = _load("model_list.json")
    obj = ListResponse[Model].model_validate(raw)
    assert obj.object == "list"
    assert len(obj.data) == 2
    assert obj.data[0].id == "openai/gpt-oss-120b-4x"
    assert obj.data[0].model_type == "COMPLETIONS"
    assert obj.data[1].model_engine == "INFINITY"


def test_validation_error_parses_into_pais_validation_error() -> None:
    body = _load("error_validation.json")
    err = error_from_response(422, body, request_id="req_1")
    assert isinstance(err, PaisValidationError)
    assert err.request_id == "req_1"
    assert err.details and err.details[0].error_code == "value_error.missing"
    assert err.details[0].loc == ["body", "name"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, PaisValidationError),
        (401, PaisAuthError),
        (403, PaisAuthError),
        (404, PaisNotFoundError),
        (422, PaisValidationError),
        (429, PaisRateLimitError),
        (500, PaisServerError),
        (502, PaisServerError),
        (503, PaisServerError),
        (418, PaisError),  # fallback
    ],
)
def test_status_to_subclass_routing(status: int, expected: type) -> None:
    err = error_from_response(status, {"detail": "boom"})
    assert isinstance(err, expected)


def test_rate_limit_carries_retry_after() -> None:
    err = error_from_response(429, {"detail": "slow down"}, retry_after=3.5)
    assert isinstance(err, PaisRateLimitError)
    assert err.retry_after == 3.5


def test_unknown_fields_tolerated() -> None:
    """Forward compat: new PAIS fields should not break deserialization."""
    raw = _load("knowledge_base.json") | {"newly_added_field": "future-value"}
    kb = KnowledgeBase.model_validate(raw)
    # Pydantic with extra="allow" keeps it accessible.
    dumped = kb.model_dump(mode="json")
    assert dumped.get("newly_added_field") == "future-value"
