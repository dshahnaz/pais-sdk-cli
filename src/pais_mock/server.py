"""FastAPI app that wraps `Store` and mirrors real PAIS paths exactly.

The request/response contract is identical to what the SDK validates — both
sides import from `pais.models`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import IO, Any

from fastapi import FastAPI, Request, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse

from pais_mock.state import Store, _HttpError


def build_app(store: Store | None = None, *, seed: str | Path | None = None) -> FastAPI:
    s = store or Store()
    if seed is not None:
        s.load_seed(seed)

    app = FastAPI(title="PAIS mock", version="0.1.0")

    # Failure-injection knobs set via special headers (test-only).
    #   X-Mock-Fail-Once: 502   → next request to the path returns 502 once
    fail_budget: dict[str, int] = {}

    async def _as_json(req: Request) -> Any:
        if req.headers.get("content-type", "").startswith("application/json"):
            try:
                return await req.json()
            except Exception:
                return None
        return None

    def _record_id(req: Request) -> str:
        return req.headers.get("x-request-id", "mock")

    @app.middleware("http")
    async def inject_request_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        rid = _record_id(request)
        # Per-path one-time failure injection
        if request.headers.get("x-mock-fail-once"):
            key = f"{request.method} {request.url.path}"
            remaining = fail_budget.get(key, 0)
            if remaining == 0 and request.headers.get("x-mock-fail-once"):
                fail_budget[key] = 1
                remaining = 1
            if remaining > 0:
                fail_budget[key] = remaining - 1
                return JSONResponse(
                    {"detail": "mock injected failure"},
                    status_code=int(request.headers["x-mock-fail-once"]),
                    headers={"X-Request-ID": rid},
                )
        response: Response = await call_next(request)
        response.headers.setdefault("X-Request-ID", rid)
        return response

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # File upload needs a dedicated route so FastAPI parses multipart properly.
    @app.post("/api/v1/control/knowledge-bases/{kb_id}/indexes/{index_id}/documents")
    async def upload_document(
        kb_id: str, index_id: str, file: UploadFile, request: Request
    ) -> Response:
        content = await file.read()
        import io

        files: dict[str, tuple[str, IO[bytes], str]] = {
            "file": (
                file.filename or "upload",
                io.BytesIO(content),
                file.content_type or "application/octet-stream",
            )
        }
        try:
            body = s._route(
                "POST",
                f"/control/knowledge-bases/{kb_id}/indexes/{index_id}/documents",
                json=None,
                params=None,
                files=files,
            )
        except _HttpError as e:
            return JSONResponse(
                e.payload, status_code=e.status, headers={"X-Request-ID": _record_id(request)}
            )
        return JSONResponse(body, headers={"X-Request-ID": _record_id(request)})

    # Streaming chat — dedicated route so we can return SSE.
    @app.post("/api/v1/compatibility/openai/v1/agents/{agent_id}/chat/completions")
    async def agent_chat(agent_id: str, request: Request) -> Response:
        payload = await _as_json(request) or {}
        path = f"/compatibility/openai/v1/agents/{agent_id}/chat/completions"
        if payload.get("stream"):

            async def gen() -> AsyncIterator[bytes]:
                for chunk in s.stream("POST", path, json=payload):
                    yield chunk

            return StreamingResponse(
                gen(), media_type="text/event-stream", headers={"X-Request-ID": _record_id(request)}
            )
        try:
            body = s._route("POST", path, json=payload, params=None, files=None)
        except _HttpError as e:
            return JSONResponse(
                e.payload, status_code=e.status, headers={"X-Request-ID": _record_id(request)}
            )
        return JSONResponse(body, headers={"X-Request-ID": _record_id(request)})

    # Catch-all under /api/v1/* routes to the Store for GET/POST/DELETE.
    @app.api_route("/api/v1/{full_path:path}", methods=["GET", "POST", "DELETE"])
    async def passthrough(full_path: str, request: Request) -> Response:
        path = "/" + full_path
        payload = await _as_json(request)
        params = dict(request.query_params) or None
        try:
            body = s._route(request.method, path, json=payload, params=params, files=None)
        except _HttpError as e:
            return JSONResponse(
                e.payload, status_code=e.status, headers={"X-Request-ID": _record_id(request)}
            )
        return JSONResponse(body, headers={"X-Request-ID": _record_id(request)})

    app.state.store = s
    return app
