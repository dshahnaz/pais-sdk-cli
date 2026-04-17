"""FakeTransport — in-process transport that routes to a MockBackend.

The backend is the same in-memory store the mock HTTP server uses, so tests
and the mock server exercise identical behavior.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import IO, Any, Protocol

from pais.errors import error_from_response
from pais.logging import current_request_id, get_logger, new_request_id
from pais.transport.base import Response

_log = get_logger("pais.transport.fake")


class MockBackend(Protocol):
    """What a FakeTransport talks to — implemented by pais_mock.state.Store."""

    def dispatch(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        files: dict[str, tuple[str, IO[bytes], str]] | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any, dict[str, str]]: ...

    def stream(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Iterator[bytes]: ...


class FakeTransport:
    def __init__(self, backend: MockBackend) -> None:
        self._backend = backend

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
    ) -> Response:
        request_id = current_request_id() or new_request_id()
        merged_headers = dict(headers or {})
        merged_headers.setdefault("X-Request-ID", request_id)
        status, body, resp_headers = self._backend.dispatch(
            method,
            path,
            json=json,
            params=params,
            files=files,
            data=data,
            headers=merged_headers,
        )
        resp_request_id = resp_headers.get("X-Request-ID") or request_id
        _log.debug(
            "pais.request",
            method=method,
            path=path,
            status=status,
            attempt=1,
            latency_ms=0,
            request_id=resp_request_id,
        )
        if status >= 400:
            retry_after = resp_headers.get("Retry-After")
            raise error_from_response(
                status,
                body,
                request_id=resp_request_id,
                retry_after=float(retry_after) if retry_after else None,
            )
        return Response(
            status_code=status, body=body, headers=resp_headers, request_id=resp_request_id
        )

    def stream(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Iterator[bytes]:
        yield from self._backend.stream(method, path, json=json, params=params, headers=headers)

    def close(self) -> None:
        return None
