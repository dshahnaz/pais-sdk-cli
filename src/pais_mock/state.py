"""In-memory PAIS state with deterministic IDs. Implements both the MockBackend
protocol (for FakeTransport) and provides helpers for the FastAPI mock server."""

from __future__ import annotations

import itertools
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from pais_mock.behaviors import chunk_text, cosine, fake_embed


@dataclass
class _Chunk:
    id: str
    document_id: str
    text: str
    embedding: list[float]


@dataclass
class _DocumentRecord:
    id: str
    created_at: int
    index_id: str
    origin_name: str
    state: str
    size_bytes: int
    chunks: list[_Chunk] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": "document",
            "created_at": self.created_at,
            "index_id": self.index_id,
            "origin_name": self.origin_name,
            "state": self.state,
            "size_bytes": self.size_bytes,
            "chunk_count": len(self.chunks),
        }


@dataclass
class _IndexingRecord:
    id: str
    created_at: int
    index_id: str
    state: str
    started_at: int | None = None
    finished_at: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": "indexing",
            "created_at": self.created_at,
            "index_id": self.index_id,
            "state": self.state,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


@dataclass
class _IndexRecord:
    id: str
    created_at: int
    kb_id: str
    name: str
    description: str | None
    embeddings_model_endpoint: str
    text_splitting: str
    chunk_size: int
    chunk_overlap: int
    status: str = "AVAILABLE"
    documents: dict[str, _DocumentRecord] = field(default_factory=dict)
    active_indexing: _IndexingRecord | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": "index",
            "created_at": self.created_at,
            "kb_id": self.kb_id,
            "name": self.name,
            "description": self.description,
            "embeddings_model_endpoint": self.embeddings_model_endpoint,
            "text_splitting": self.text_splitting,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
            "status": self.status,
        }


@dataclass
class _KBRecord:
    id: str
    created_at: int
    name: str
    description: str | None
    data_origin_type: str
    index_refresh_policy: dict[str, Any]
    indexes: dict[str, _IndexRecord] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "object": "knowledge_base",
            "created_at": self.created_at,
            "name": self.name,
            "description": self.description,
            "data_origin_type": self.data_origin_type,
            "index_refresh_policy": self.index_refresh_policy,
        }


@dataclass
class _AgentRecord:
    id: str
    created_at: int
    name: str
    description: str | None
    model: str
    instructions: str | None
    tools: list[dict[str, Any]]
    payload: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        base = {
            "id": self.id,
            "object": "agent",
            "created_at": self.created_at,
            "name": self.name,
            "description": self.description,
            "model": self.model,
            "instructions": self.instructions,
            "tools": self.tools,
            "status": "READY",
        }
        # Merge any additional fields captured from the create payload.
        for k, v in self.payload.items():
            base.setdefault(k, v)
        return base


_KB_RE = re.compile(r"^/control/knowledge-bases(?:/(?P<kb>[^/]+))?(?P<rest>/.*)?$")


class Store:
    """In-memory PAIS backend. Thread-unsafe — single-process use only."""

    def __init__(self) -> None:
        self._kbs: dict[str, _KBRecord] = {}
        self._agents: dict[str, _AgentRecord] = {}
        self._mcp_tools: list[dict[str, Any]] = [
            {
                "id": "mcp_tool_kbsearch_default",
                "object": "mcp_tool",
                "name": "knowledge_base_index_search",
                "description": "Search the knowledge base index for relevant chunks.",
                "server": "built-in",
            }
        ]
        self._models: list[dict[str, Any]] = [
            {
                "id": "openai/gpt-oss-120b-4x",
                "object": "model",
                "model_type": "COMPLETIONS",
                "model_engine": "VLLM",
                "owned_by": "pais",
            },
            {
                "id": "BAAI/bge-small-en-v1.5",
                "object": "model",
                "model_type": "EMBEDDINGS",
                "model_engine": "INFINITY",
                "owned_by": "pais",
            },
            {
                "id": "local/llama-cpp-7b",
                "object": "model",
                "model_type": "COMPLETIONS",
                "model_engine": "LLAMA_CPP",
                "owned_by": "pais",
            },
        ]
        self._created = itertools.count(1_700_000_000)
        self._kb_ids = itertools.count(1)
        self._ix_ids = itertools.count(1)
        self._doc_ids = itertools.count(1)
        self._ixn_ids = itertools.count(1)
        self._agent_ids = itertools.count(1)
        # Per-index tool binding: index_id -> mcp_tool id (for KB search)
        self._index_tool_binding: dict[str, str] = {}
        # Test hook: paths in this set return 404 from `_route` so callers can
        # exercise the probe-then-fallback paths in cleanup/cancel ops.
        # Each entry is a tuple of (METHOD, path-suffix). The suffix matches by
        # `endswith` so callers don't have to know the exact kb/index ids.
        self.disabled_endpoints: set[tuple[str, str]] = set()

    # --- MockBackend protocol -------------------------------------------------
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
    ) -> tuple[int, Any, dict[str, str]]:
        method = method.upper()
        resp_headers = {"X-Request-ID": (headers or {}).get("X-Request-ID", "mock")}
        try:
            body = self._route(method, path, json=json, params=params, files=files)
        except _HttpError as e:
            return e.status, e.payload, resp_headers
        return 200, body, resp_headers

    def stream(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Iterator[bytes]:
        # Only chat completions stream is supported.
        if "/chat/completions" not in path:
            raise _HttpError(405, {"detail": "streaming not supported for this path"})
        resp = self._chat_completion(path, json or {}, stream=True)
        for event in resp:
            yield f"data: {event}\n\n".encode()
        yield b"data: [DONE]\n\n"

    # --- Routing --------------------------------------------------------------
    def _route(
        self,
        method: str,
        path: str,
        *,
        json: Any,
        params: dict[str, Any] | None,
        files: dict[str, tuple[str, IO[bytes], str]] | None,
    ) -> Any:
        if path.startswith("/compatibility/openai/v1/models"):
            return {"object": "list", "data": self._models, "has_more": False}
        if path.startswith("/compatibility/openai/v1/embeddings"):
            return self._embeddings(json or {})
        if path.startswith("/compatibility/openai/v1/agents"):
            return self._agents_route(method, path, json, files)
        if path.startswith("/compatibility/openai/v1/chat/completions"):
            return self._chat_completion(path, json or {}, stream=False)
        if path.startswith("/control/mcp-servers/tools"):
            return self._mcp_tools_list()
        if path.startswith("/control/data-sources"):
            return self._data_sources_route(method, path, json)
        if path.startswith("/control/knowledge-bases"):
            return self._kb_route(method, path, json, params, files)
        raise _HttpError(404, {"detail": f"no route: {method} {path}"})

    # --- MCP tools ------------------------------------------------------------
    def _mcp_tools_list(self) -> dict[str, Any]:
        return {"object": "list", "data": self._mcp_tools, "has_more": False}

    # --- Data sources ---------------------------------------------------------
    def _data_sources_route(self, method: str, path: str, json: Any) -> Any:
        if method == "GET" and path == "/control/data-sources":
            return {"object": "list", "data": [], "has_more": False}
        raise _HttpError(404, {"detail": f"data-sources route not implemented: {method} {path}"})

    # --- Knowledge bases (and nested indexes/documents/indexings/search) -----
    def _kb_route(
        self,
        method: str,
        path: str,
        json: Any,
        params: dict[str, Any] | None,
        files: dict[str, tuple[str, IO[bytes], str]] | None,
    ) -> Any:
        m = _KB_RE.match(path)
        if not m:
            raise _HttpError(404, {"detail": f"bad kb path: {path}"})
        kb_id = m.group("kb")
        rest = m.group("rest") or ""

        # Top-level KB collection
        if kb_id is None:
            if method == "GET":
                return {
                    "object": "list",
                    "data": [kb.to_json() for kb in self._kbs.values()],
                    "has_more": False,
                }
            if method == "POST":
                return self._create_kb(json or {})
            raise _HttpError(405, {"detail": f"method not allowed: {method}"})

        kb = self._kbs.get(kb_id)
        if kb is None:
            raise _HttpError(404, {"detail": f"knowledge base not found: {kb_id}"})

        # Single KB
        if rest == "":
            if method == "GET":
                return kb.to_json()
            if method == "POST":
                return self._update_kb(kb, json or {})
            if method == "DELETE":
                del self._kbs[kb_id]
                # Doc-aligned response body.
                return {"id": kb_id, "object": "knowledge_base.deleted", "deleted": True}
            raise _HttpError(405, {"detail": f"method not allowed: {method}"})

        # Nested: /indexes/... or /data-sources
        return self._kb_nested(method, kb, rest, json, params, files)

    def _create_kb(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not payload.get("name"):
            raise _HttpError(422, {"detail": [{"error_code": "missing", "loc": ["body", "name"]}]})
        kb_id = f"kb_{next(self._kb_ids)}"
        kb = _KBRecord(
            id=kb_id,
            created_at=next(self._created),
            name=payload["name"],
            description=payload.get("description"),
            data_origin_type=payload.get("data_origin_type", "LOCAL_FILES"),
            index_refresh_policy=payload.get("index_refresh_policy", {"policy_type": "MANUAL"}),
        )
        self._kbs[kb_id] = kb
        return kb.to_json()

    def _update_kb(self, kb: _KBRecord, payload: dict[str, Any]) -> dict[str, Any]:
        for k in ("name", "description", "index_refresh_policy"):
            if k in payload and payload[k] is not None:
                setattr(kb, k, payload[k])
        return kb.to_json()

    def _kb_nested(
        self,
        method: str,
        kb: _KBRecord,
        rest: str,
        json: Any,
        params: dict[str, Any] | None,
        files: dict[str, tuple[str, IO[bytes], str]] | None,
    ) -> Any:
        if rest == "/data-sources":
            if method == "GET":
                return {"object": "list", "data": [], "has_more": False}
            if method == "POST":
                return {"linked": True}
            raise _HttpError(405, {"detail": f"method not allowed: {method}"})

        if not rest.startswith("/indexes"):
            raise _HttpError(404, {"detail": f"unknown nested path: {rest}"})

        ix_rest = rest[len("/indexes") :]
        # Cases: "", "/{id}", "/{id}/indexings", "/{id}/active-indexing",
        #        "/{id}/documents", "/{id}/search"
        if ix_rest == "":
            if method == "GET":
                return {
                    "object": "list",
                    "data": [ix.to_json() for ix in kb.indexes.values()],
                    "has_more": False,
                }
            if method == "POST":
                return self._create_index(kb, json or {})
            raise _HttpError(405, {"detail": f"method not allowed: {method}"})

        parts = ix_rest.lstrip("/").split("/")
        index_id = parts[0]
        ix = kb.indexes.get(index_id)
        if ix is None:
            raise _HttpError(404, {"detail": f"index not found: {index_id}"})
        sub = parts[1] if len(parts) > 1 else ""

        if sub == "":
            if method == "GET":
                return ix.to_json()
            if method == "POST":
                return self._update_index(ix, json or {})
            if method == "DELETE":
                del kb.indexes[index_id]
                return {"deleted": True, "id": index_id}
            raise _HttpError(405, {"detail": f"method not allowed: {method}"})

        if sub == "indexings":
            if method != "POST":
                raise _HttpError(405, {"detail": "indexings requires POST"})
            return self._trigger_indexing(ix)
        if sub == "active-indexing":
            if method == "GET":
                if ix.active_indexing is None:
                    raise _HttpError(404, {"detail": "no active indexing"})
                return ix.active_indexing.to_json()
            if method == "DELETE":
                self._check_disabled(method, "/active-indexing")
                if ix.active_indexing is None:
                    raise _HttpError(404, {"detail": "no active indexing"})
                ix.active_indexing.state = "CANCELLED"
                cancelled = ix.active_indexing
                ix.active_indexing = None
                return cancelled.to_json()
            raise _HttpError(405, {"detail": f"active-indexing method not allowed: {method}"})
        if sub == "documents":
            # /documents OR /documents/{document_id}
            if len(parts) == 2:
                if method == "GET":
                    return self._list_documents_page(ix, params)
                if method == "POST":
                    return self._upload_document(ix, files)
                raise _HttpError(405, {"detail": f"method not allowed: {method}"})
            doc_id = parts[2]
            if method == "DELETE":
                self._check_disabled(method, "/documents/{id}")
                if doc_id not in ix.documents:
                    raise _HttpError(404, {"detail": f"document not found: {doc_id}"})
                del ix.documents[doc_id]
                return {"deleted": True, "id": doc_id}
            raise _HttpError(405, {"detail": f"document {doc_id}: method not allowed: {method}"})
        if sub == "search":
            if method != "POST":
                raise _HttpError(405, {"detail": "search requires POST"})
            return self._search(ix, json or {})
        raise _HttpError(404, {"detail": f"unknown index sub-route: {sub}"})

    def _check_disabled(self, method: str, suffix: str) -> None:
        """Test hook: simulate a PAIS deployment that doesn't expose an endpoint."""
        if (method, suffix) in self.disabled_endpoints:
            raise _HttpError(405, {"detail": f"endpoint disabled in mock: {method} {suffix}"})

    def _create_index(self, kb: _KBRecord, payload: dict[str, Any]) -> dict[str, Any]:
        if not payload.get("name") or not payload.get("embeddings_model_endpoint"):
            raise _HttpError(
                422,
                {
                    "detail": [
                        {
                            "error_code": "missing",
                            "loc": ["body", "name/embeddings_model_endpoint"],
                        }
                    ]
                },
            )
        ix_id = f"idx_{next(self._ix_ids)}"
        ix = _IndexRecord(
            id=ix_id,
            created_at=next(self._created),
            kb_id=kb.id,
            name=payload["name"],
            description=payload.get("description"),
            embeddings_model_endpoint=payload["embeddings_model_endpoint"],
            text_splitting=payload.get("text_splitting", "SENTENCE"),
            chunk_size=int(payload.get("chunk_size", 400)),
            chunk_overlap=int(payload.get("chunk_overlap", 100)),
        )
        kb.indexes[ix_id] = ix
        # Auto-provision a per-index KB search MCP tool entry.
        tool = {
            "id": f"mcp_tool_kbsearch_{ix_id}",
            "object": "mcp_tool",
            "name": f"knowledge_base_index_search_{ix_id}",
            "description": f"Search knowledge base index {ix_id} for relevant chunks.",
            "server": "built-in",
        }
        self._mcp_tools.append(tool)
        self._index_tool_binding[ix_id] = tool["id"]
        return ix.to_json()

    def _update_index(self, ix: _IndexRecord, payload: dict[str, Any]) -> dict[str, Any]:
        for k in (
            "name",
            "description",
            "embeddings_model_endpoint",
            "text_splitting",
            "chunk_size",
            "chunk_overlap",
        ):
            if k in payload and payload[k] is not None:
                setattr(ix, k, payload[k])
        return ix.to_json()

    def _upload_document(
        self,
        ix: _IndexRecord,
        files: dict[str, tuple[str, IO[bytes], str]] | None,
    ) -> dict[str, Any]:
        if not files or "file" not in files:
            raise _HttpError(422, {"detail": [{"error_code": "missing", "loc": ["file"]}]})
        name, stream, _ctype = files["file"]
        content = stream.read()
        text = content.decode("utf-8", errors="replace")
        doc_id = f"doc_{next(self._doc_ids)}"
        doc = _DocumentRecord(
            id=doc_id,
            created_at=next(self._created),
            index_id=ix.id,
            origin_name=name,
            state="PENDING",
            size_bytes=len(content),
        )
        # Synchronous "indexing" to keep things simple for tests. A polling
        # caller will see a completed `active-indexing` on the first request.
        chunks = chunk_text(text, chunk_size=ix.chunk_size, chunk_overlap=ix.chunk_overlap)
        for i, c in enumerate(chunks):
            doc.chunks.append(
                _Chunk(
                    id=f"{doc_id}_c{i}",
                    document_id=doc_id,
                    text=c,
                    embedding=fake_embed(c),
                )
            )
        doc.state = "INDEXED"
        ix.documents[doc_id] = doc
        # Seed a completed active-indexing so pollers finish cleanly.
        ixn_id = f"ixn_{next(self._ixn_ids)}"
        now = next(self._created)
        ix.active_indexing = _IndexingRecord(
            id=ixn_id,
            created_at=now,
            index_id=ix.id,
            state="DONE",
            started_at=now,
            finished_at=now,
        )
        return doc.to_json()

    def _list_documents_page(
        self, ix: _IndexRecord, params: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Cursor-paginated document list.

        Honors ?limit=N&after=<doc_id>. Defaults mimic a typical PAIS server:
        `limit` defaults to 100, `after` is the cursor from the previous page's
        `last_id`. Response always includes `has_more` and, when more pages
        remain, `first_id` + `last_id`.
        """
        q = params or {}
        try:
            limit = int(q.get("limit", 100))
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 1000))
        after = q.get("after")

        all_docs = list(ix.documents.values())  # insertion order == creation order
        start = 0
        if after:
            for i, d in enumerate(all_docs):
                if d.id == after:
                    start = i + 1
                    break
            else:
                # Cursor not found — treat as an empty tail, same as real servers.
                start = len(all_docs)
        page = all_docs[start : start + limit]
        has_more = start + limit < len(all_docs)
        body: dict[str, Any] = {
            "object": "list",
            "data": [d.to_json() for d in page],
            "has_more": has_more,
            "num_objects": len(all_docs),
        }
        if page:
            body["first_id"] = page[0].id
            body["last_id"] = page[-1].id
        return body

    def _trigger_indexing(self, ix: _IndexRecord) -> dict[str, Any]:
        ixn_id = f"ixn_{next(self._ixn_ids)}"
        now = next(self._created)
        ix.active_indexing = _IndexingRecord(
            id=ixn_id,
            created_at=now,
            index_id=ix.id,
            state="DONE",
            started_at=now,
            finished_at=now,
        )
        return ix.active_indexing.to_json()

    def _search(self, ix: _IndexRecord, payload: dict[str, Any]) -> dict[str, Any]:
        # Doc-aligned wire format: {text, top_k, similarity_cutoff}.
        # Tolerate the legacy {query, top_n} shape too so older clients still work.
        query = payload.get("text") or payload.get("query") or ""
        top_n = int(payload.get("top_k") or payload.get("top_n") or 5)
        cutoff = float(payload.get("similarity_cutoff", 0.0))
        q_vec = fake_embed(query)
        hits: list[tuple[float, _Chunk, _DocumentRecord]] = []
        for doc in ix.documents.values():
            for chunk in doc.chunks:
                score = cosine(q_vec, chunk.embedding)
                if score >= cutoff:
                    hits.append((score, chunk, doc))
        hits.sort(key=lambda x: x[0], reverse=True)
        hits = hits[:top_n]
        return {
            "object": "search_result",
            # Doc-aligned response key; SDK normalizes `chunks` → `hits`.
            "chunks": [
                {
                    "document_id": c.document_id,
                    "origin_name": d.origin_name,
                    "origin_ref": d.origin_name,
                    "media_type": "text/markdown",
                    "score": round(s, 6),
                    "text": c.text,
                    # Legacy back-compat for clients that still expect chunk_id.
                    "chunk_id": c.id,
                }
                for s, c, d in hits
            ],
        }

    # --- Agents ---------------------------------------------------------------
    def _agents_route(
        self,
        method: str,
        path: str,
        json: Any,
        files: dict[str, tuple[str, IO[bytes], str]] | None,
    ) -> Any:
        tail = path[len("/compatibility/openai/v1/agents") :]
        if tail == "" or tail == "/":
            if method == "GET":
                return {
                    "object": "list",
                    "data": [a.to_json() for a in self._agents.values()],
                    "has_more": False,
                }
            if method == "POST":
                return self._create_agent(json or {})
            raise _HttpError(405, {"detail": f"method not allowed: {method}"})

        parts = tail.lstrip("/").split("/")
        agent_id = parts[0]
        agent = self._agents.get(agent_id)
        if agent is None:
            raise _HttpError(404, {"detail": f"agent not found: {agent_id}"})
        sub = "/".join(parts[1:])
        if sub == "":
            if method == "GET":
                return agent.to_json()
            if method == "POST":
                return self._update_agent(agent, json or {})
            if method == "DELETE":
                del self._agents[agent_id]
                # Doc-aligned response body.
                return {"id": agent_id, "object": "agent.deleted", "deleted": True}
            raise _HttpError(405, {"detail": f"method not allowed: {method}"})
        if sub == "chat/completions":
            return self._chat_completion(path, json or {}, stream=False, agent=agent)
        raise _HttpError(404, {"detail": f"unknown agent sub-route: {sub}"})

    def _create_agent(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not payload.get("name") or not payload.get("model"):
            raise _HttpError(
                422, {"detail": [{"error_code": "missing", "loc": ["body", "name/model"]}]}
            )
        agent_id = f"agent_{next(self._agent_ids)}"
        agent = _AgentRecord(
            id=agent_id,
            created_at=next(self._created),
            name=payload["name"],
            description=payload.get("description"),
            model=payload["model"],
            instructions=payload.get("instructions"),
            tools=payload.get("tools", []),
            payload={
                k: v
                for k, v in payload.items()
                if k not in {"name", "description", "model", "instructions", "tools"}
            },
        )
        self._agents[agent_id] = agent
        return agent.to_json()

    def _update_agent(self, agent: _AgentRecord, payload: dict[str, Any]) -> dict[str, Any]:
        for k in ("name", "description", "model", "instructions", "tools"):
            if k in payload and payload[k] is not None:
                setattr(agent, k, payload[k])
        return agent.to_json()

    # --- Chat completions -----------------------------------------------------
    def _chat_completion(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        stream: bool,
        agent: _AgentRecord | None = None,
    ) -> Any:
        messages: list[dict[str, Any]] = payload.get("messages", []) or []
        user_q = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        refs: list[dict[str, Any]] = []
        augmentation = ""
        if agent is not None:
            # For each KB-search tool linked on the agent, run the search and
            # include a synthetic answer that references the top chunks. This
            # makes RAG wiring testable.
            for tool in agent.tools:
                if tool.get("link_type") != "PAIS_KNOWLEDGE_BASE_INDEX_SEARCH_TOOL_LINK":
                    continue
                tool_id = tool.get("tool_id")
                # Find the index bound to this tool.
                for ix_id, bound in self._index_tool_binding.items():
                    if bound != tool_id:
                        continue
                    # Locate the index across KBs.
                    ix = next(
                        (kb.indexes[ix_id] for kb in self._kbs.values() if ix_id in kb.indexes),
                        None,
                    )
                    if ix is None:
                        continue
                    res = self._search(
                        ix,
                        {
                            "query": user_q,
                            "top_n": tool.get("top_n", 3),
                            "similarity_cutoff": tool.get("similarity_cutoff", 0.0),
                        },
                    )
                    # Search returns the doc-aligned `chunks` key now.
                    for hit in res.get("chunks") or res.get("hits") or []:
                        refs.append(hit)
                        augmentation += f"\n- {hit['text'][:160]}"

        model_id = payload.get("model") or (agent.model if agent else self._models[0]["id"])
        answer = f"(mock) You asked: {user_q!r}."
        if augmentation:
            answer += f" Retrieved context:{augmentation}"

        if stream:
            # Emit a couple of SSE events with the answer split in halves.
            mid = len(answer) // 2 or 1
            return [
                json.dumps(
                    {
                        "id": "chatcmpl-mock",
                        "object": "chat.completion.chunk",
                        "created": next(self._created),
                        "model": model_id,
                        "choices": [
                            {"index": 0, "delta": {"role": "assistant", "content": answer[:mid]}}
                        ],
                    }
                ),
                json.dumps(
                    {
                        "id": "chatcmpl-mock",
                        "object": "chat.completion.chunk",
                        "created": next(self._created),
                        "model": model_id,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": answer[mid:]},
                                "finish_reason": "stop",
                            }
                        ],
                    }
                ),
            ]

        return {
            "id": f"chatcmpl-mock-{next(self._created)}",
            "object": "chat.completion",
            "created": next(self._created),
            "model": model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": answer},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": len(user_q.split()),
                "completion_tokens": len(answer.split()),
                "total_tokens": len(user_q.split()) + len(answer.split()),
            },
            "references": refs or None,
        }

    # --- Embeddings -----------------------------------------------------------
    def _embeddings(self, payload: dict[str, Any]) -> dict[str, Any]:
        inp = payload.get("input")
        if inp is None:
            raise _HttpError(422, {"detail": [{"error_code": "missing", "loc": ["body", "input"]}]})
        items = inp if isinstance(inp, list) else [inp]
        return {
            "object": "list",
            "model": payload.get("model", self._models[1]["id"]),
            "data": [
                {"object": "embedding", "index": i, "embedding": fake_embed(str(x))}
                for i, x in enumerate(items)
            ],
        }

    # --- Seeding --------------------------------------------------------------
    def load_seed(self, path: str | Path) -> None:
        """Seed the store from a JSON fixture. Schema:

        {
          "knowledge_bases": [{ "name": "...", "indexes": [
               {"name": "...", "embeddings_model_endpoint": "...",
                "documents": [{"origin_name": "x.txt", "text": "..."}]}
          ]}]
        }
        """
        raw = json.loads(Path(path).read_text())
        for kb_spec in raw.get("knowledge_bases", []):
            kb = self._route(
                "POST", "/control/knowledge-bases", json=kb_spec, params=None, files=None
            )
            kb_id = kb["id"]
            for ix_spec in kb_spec.get("indexes", []):
                ix = self._route(
                    "POST",
                    f"/control/knowledge-bases/{kb_id}/indexes",
                    json={k: v for k, v in ix_spec.items() if k != "documents"},
                    params=None,
                    files=None,
                )
                ix_id = ix["id"]
                for doc_spec in ix_spec.get("documents", []):
                    text = doc_spec.get("text", "")
                    name = doc_spec["origin_name"]
                    import io

                    self._upload_document(
                        self._kbs[kb_id].indexes[ix_id],
                        {"file": (name, io.BytesIO(text.encode("utf-8")), "text/plain")},
                    )


class _HttpError(Exception):
    def __init__(self, status: int, payload: Any) -> None:
        self.status = status
        self.payload = payload
