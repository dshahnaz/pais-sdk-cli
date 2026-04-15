"""Structured error hierarchy mapped from PAIS `detail[]` responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ErrorDetail:
    error_code: str | None = None
    loc: list[str | int] | None = None
    value: Any = None
    msg: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ErrorDetail:
        return cls(
            error_code=raw.get("error_code"),
            loc=raw.get("loc"),
            value=raw.get("value"),
            msg=raw.get("msg") or raw.get("message"),
        )


class PaisError(Exception):
    """Base PAIS SDK error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        details: list[ErrorDetail] | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.details = details or []
        self.request_id = request_id

    def __str__(self) -> str:
        base = super().__str__()
        parts = [base]
        if self.status_code is not None:
            parts.append(f"status={self.status_code}")
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        if self.details:
            codes = ",".join(d.error_code or "?" for d in self.details)
            parts.append(f"codes=[{codes}]")
        return " | ".join(parts)


class PaisAuthError(PaisError):
    """401/403 — auth failed or insufficient."""


class PaisNotFoundError(PaisError):
    """404 — resource not found."""


class PaisValidationError(PaisError):
    """400/422 — request failed validation."""


class PaisRateLimitError(PaisError):
    """429 — rate-limited. `retry_after` in seconds if server provided it."""

    def __init__(self, *args: Any, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


class PaisServerError(PaisError):
    """5xx — server-side failure."""


class PaisTimeoutError(PaisError):
    """Transport-level timeout."""


_STATUS_MAP: dict[int, type[PaisError]] = {
    400: PaisValidationError,
    401: PaisAuthError,
    403: PaisAuthError,
    404: PaisNotFoundError,
    422: PaisValidationError,
    429: PaisRateLimitError,
}


def error_from_response(
    status_code: int,
    body: Any,
    *,
    request_id: str | None = None,
    retry_after: float | None = None,
) -> PaisError:
    """Parse a PAIS error response body into the right subclass."""
    details: list[ErrorDetail] = []
    message = f"PAIS request failed with status {status_code}"

    if isinstance(body, dict):
        raw_details = body.get("detail")
        if isinstance(raw_details, list):
            details = [
                ErrorDetail.from_dict(d) if isinstance(d, dict) else ErrorDetail(msg=str(d))
                for d in raw_details
            ]
        elif isinstance(raw_details, str):
            message = raw_details
        if body.get("message"):
            message = str(body["message"])

    cls: type[PaisError] = (
        PaisServerError if status_code >= 500 else _STATUS_MAP.get(status_code, PaisError)
    )

    kwargs: dict[str, Any] = {
        "status_code": status_code,
        "details": details,
        "request_id": request_id,
    }
    if cls is PaisRateLimitError:
        return PaisRateLimitError(message, retry_after=retry_after, **kwargs)
    return cls(message, **kwargs)
