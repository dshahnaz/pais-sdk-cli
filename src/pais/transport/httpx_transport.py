"""Real HTTP transport: httpx + retries + timeouts + TLS toggle + SSE streaming."""

from __future__ import annotations

import random
import time
import warnings
from collections.abc import Iterator
from typing import IO, Any

import httpx

from pais.auth.base import AuthStrategy
from pais.auth.none import NoAuth
from pais.errors import PaisTimeoutError, error_from_response
from pais.logging import current_request_id, get_logger, new_request_id
from pais.transport.base import Response

_log = get_logger("pais.transport")

RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_CHAT_PATH_MARKER = "/chat/completions"


class HttpxTransport:
    def __init__(
        self,
        base_url: str,
        *,
        auth: AuthStrategy | None = None,
        verify_ssl: bool = True,
        connect_timeout: float = 5.0,
        read_timeout: float = 60.0,
        total_timeout: float = 120.0,
        retry_max_attempts: int = 4,
        retry_base_delay: float = 0.25,
        retry_max_delay: float = 10.0,
        chat_cold_start_retries: int = 3,
        chat_cold_start_delay: float = 3.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth: AuthStrategy = auth or NoAuth()
        self.verify_ssl = verify_ssl
        self.retry_max_attempts = retry_max_attempts
        self.retry_base_delay = retry_base_delay
        self.retry_max_delay = retry_max_delay
        self.chat_cold_start_retries = chat_cold_start_retries
        self.chat_cold_start_delay = chat_cold_start_delay
        self._total_timeout = total_timeout

        if not verify_ssl:
            _log.warning(
                "pais.tls.verification_disabled",
                base_url=self.base_url,
                note="self-signed certificates accepted; do not use in production-internet",
            )
            # Suppress urllib3 warning spam (users often re-vendor it).
            try:
                import urllib3

                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass
            warnings.filterwarnings("ignore", category=DeprecationWarning)

        self._client = httpx.Client(
            base_url=self.base_url,
            verify=verify_ssl,
            timeout=httpx.Timeout(
                total_timeout, connect=connect_timeout, read=read_timeout, write=read_timeout
            ),
        )

    # --- Public API -----------------------------------------------------------
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
        return self._do_request(
            method,
            path,
            json=json,
            params=params,
            headers=headers,
            files=files,
            data=data,
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
        request_id = current_request_id() or new_request_id()
        merged = self._build_headers(headers, request_id)
        started = time.perf_counter()
        _log.info(
            "pais.request",
            method=method,
            path=path,
            stream=True,
            request_id=request_id,
        )
        with self._client.stream(method, path, json=json, params=params, headers=merged) as resp:
            if resp.status_code >= 400:
                body = self._parse_body(resp.read())
                raise error_from_response(
                    resp.status_code, body, request_id=resp.headers.get("X-Request-ID")
                )
            yield from resp.iter_bytes()
        _log.info(
            "pais.request.stream_done",
            method=method,
            path=path,
            latency_ms=int((time.perf_counter() - started) * 1000),
            request_id=request_id,
        )

    def close(self) -> None:
        self._client.close()

    # --- Internals ------------------------------------------------------------
    def _build_headers(self, headers: dict[str, str] | None, request_id: str) -> dict[str, str]:
        merged: dict[str, str] = dict(headers or {})
        self.auth.apply(merged)
        merged.setdefault("X-Request-ID", request_id)
        merged.setdefault("Accept", "application/json")
        return merged

    def _is_chat_path(self, path: str) -> bool:
        return _CHAT_PATH_MARKER in path

    def _do_request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None,
        params: dict[str, Any] | None,
        headers: dict[str, str] | None,
        files: dict[str, tuple[str, IO[bytes], str]] | None,
        data: dict[str, Any] | None,
    ) -> Response:
        request_id = current_request_id() or new_request_id()
        total_deadline = time.monotonic() + self._total_timeout

        max_attempts = self.retry_max_attempts
        # Agent chat endpoints cold-start: give them extra 502 retries.
        if self._is_chat_path(path):
            max_attempts = max(max_attempts, self.chat_cold_start_retries)

        last_err: Exception | None = None
        auth_retried = False

        for attempt in range(1, max_attempts + 1):
            if time.monotonic() > total_deadline:
                raise PaisTimeoutError(
                    f"Total timeout exceeded for {method} {path}",
                    request_id=request_id,
                )
            merged_headers = self._build_headers(headers, request_id)
            started = time.perf_counter()
            try:
                resp = self._client.request(
                    method,
                    path,
                    json=json,
                    params=params,
                    headers=merged_headers,
                    files=files,
                    data=data,
                )
            except httpx.TimeoutException as e:
                last_err = e
                _log.warning(
                    "pais.request.timeout",
                    method=method,
                    path=path,
                    attempt=attempt,
                    request_id=request_id,
                )
                if attempt >= max_attempts:
                    raise PaisTimeoutError(str(e), request_id=request_id) from e
                self._sleep_for(attempt, None)
                continue
            except httpx.RequestError as e:
                last_err = e
                _log.warning(
                    "pais.request.network_error",
                    method=method,
                    path=path,
                    attempt=attempt,
                    error=str(e),
                    request_id=request_id,
                )
                if attempt >= max_attempts:
                    raise
                self._sleep_for(attempt, None)
                continue

            latency_ms = int((time.perf_counter() - started) * 1000)
            body = self._parse_body(resp.content)
            resp_request_id = resp.headers.get("X-Request-ID") or request_id

            _log.info(
                "pais.request",
                method=method,
                path=path,
                status=resp.status_code,
                latency_ms=latency_ms,
                attempt=attempt,
                request_id=resp_request_id,
            )

            # 401 — try refreshing auth once, then retry.
            if resp.status_code == 401 and not auth_retried and self.auth.refresh():
                auth_retried = True
                continue

            # Chat cold-start: if it's a chat endpoint and we got 502, wait longer.
            if resp.status_code == 502 and self._is_chat_path(path) and attempt < max_attempts:
                _log.warning(
                    "pais.request.cold_start_retry",
                    attempt=attempt,
                    request_id=resp_request_id,
                )
                time.sleep(self.chat_cold_start_delay)
                continue

            if resp.status_code in RETRYABLE_STATUSES and attempt < max_attempts:
                retry_after = self._parse_retry_after(resp.headers.get("Retry-After"))
                self._sleep_for(attempt, retry_after)
                continue

            if resp.status_code >= 400:
                retry_after = self._parse_retry_after(resp.headers.get("Retry-After"))
                raise error_from_response(
                    resp.status_code,
                    body,
                    request_id=resp_request_id,
                    retry_after=retry_after,
                )

            return Response(
                status_code=resp.status_code,
                body=body,
                headers=dict(resp.headers),
                request_id=resp_request_id,
            )

        # Exhausted retries without a success/exception.
        if last_err is not None:
            raise last_err  # pragma: no cover
        raise PaisTimeoutError(f"Exhausted retries for {method} {path}", request_id=request_id)

    def _parse_body(self, content: bytes) -> Any:
        if not content:
            return None
        try:
            import json as _json

            return _json.loads(content)
        except Exception:
            try:
                return content.decode("utf-8", errors="replace")
            except Exception:  # pragma: no cover
                return content

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    def _sleep_for(self, attempt: int, retry_after: float | None) -> None:
        if retry_after is not None:
            time.sleep(retry_after)
            return
        # exponential backoff with jitter
        base = self.retry_base_delay * (2 ** (attempt - 1))
        delay = min(base + random.uniform(0, base), self.retry_max_delay)
        time.sleep(delay)
