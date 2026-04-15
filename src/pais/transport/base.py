"""Transport protocol — abstracts HTTP so SDK works against real PAIS or fakes."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import IO, Any, Protocol, runtime_checkable


@dataclass
class Response:
    status_code: int
    body: Any  # parsed JSON or raw text
    headers: dict[str, str] = field(default_factory=dict)
    request_id: str | None = None

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


@runtime_checkable
class Transport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        files: dict[str, tuple[str, IO[bytes], str]] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Response: ...

    def stream(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Iterator[bytes]: ...

    def close(self) -> None: ...
