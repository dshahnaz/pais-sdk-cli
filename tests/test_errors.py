"""Formatter + mapper tests for `pais.errors`.

Specifically pins the v0.6.8 change: `PaisError.__str__` surfaces the first
`ErrorDetail.loc` + `msg` so 422s aren't opaque. Without this, users see
only `codes=[VALIDATION_ERROR]` and can't tell which field the server
rejected.
"""

from __future__ import annotations

from pais.errors import (
    ErrorDetail,
    PaisValidationError,
    error_from_response,
)


def test_str_includes_first_detail_loc_and_msg() -> None:
    err = PaisValidationError(
        "PAIS request failed with status 422",
        status_code=422,
        request_id="abc-123",
        details=[
            ErrorDetail(error_code="VALIDATION_ERROR", loc=["query", "limit"], msg="field required")
        ],
    )
    s = str(err)
    assert "codes=[VALIDATION_ERROR]" in s
    assert "detail=query.limit: field required" in s
    # Still contains the standard anchors.
    assert "status=422" in s
    assert "request_id=abc-123" in s


def test_str_omits_detail_when_only_code_present() -> None:
    err = PaisValidationError(
        "boom",
        status_code=400,
        details=[ErrorDetail(error_code="SOMETHING_WRONG")],
    )
    s = str(err)
    assert "codes=[SOMETHING_WRONG]" in s
    assert "detail=" not in s


def test_str_handles_msg_without_loc() -> None:
    err = PaisValidationError(
        "boom",
        status_code=422,
        details=[ErrorDetail(msg="bad thing")],
    )
    s = str(err)
    # Empty loc → "detail=: bad thing" (still actionable: the msg is there).
    assert "detail=: bad thing" in s


def test_str_omits_value_field_even_if_detail_has_one() -> None:
    """`value` can contain request payload bits we don't want to leak."""
    err = PaisValidationError(
        "boom",
        status_code=422,
        details=[
            ErrorDetail(
                loc=["body", "password"],
                msg="invalid",
                value="plaintext-password-here",
            )
        ],
    )
    s = str(err)
    assert "plaintext-password-here" not in s
    assert "detail=body.password: invalid" in s


def test_error_from_response_422_maps_to_validation_error_with_detail() -> None:
    body = {
        "detail": [
            {"error_code": "VALIDATION_ERROR", "loc": ["query", "limit"], "msg": "field required"}
        ]
    }
    err = error_from_response(422, body, request_id="r-1")
    assert isinstance(err, PaisValidationError)
    assert "detail=query.limit: field required" in str(err)
