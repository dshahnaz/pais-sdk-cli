"""Indexes + indexings + documents + search (nested under /knowledge-bases/{kb_id})."""

from __future__ import annotations

import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, Literal

from pais.errors import IndexDeleteUnsupported, PaisError, PaisNotFoundError
from pais.logging import get_logger
from pais.models.common import ListResponse
from pais.models.index import (
    Document,
    Index,
    IndexCreate,
    Indexing,
    IndexingState,
    IndexUpdate,
    SearchHit,
    SearchQuery,
    SearchResponse,
)
from pais.resources._base import Resource

_log = get_logger("pais.indexes")

_TERMINAL_STATES = {IndexingState.DONE, IndexingState.FAILED, IndexingState.CANCELLED}

CleanupStrategy = Literal["auto", "api", "recreate"]


@dataclass
class PurgeResult:
    strategy_used: Literal["api", "recreate"]
    documents_deleted: int
    new_index_id: str | None = None  # set when recreate fallback fired
    errors: list[str] = field(default_factory=list)


@dataclass
class CancelResult:
    cancelled: bool
    strategy_used: Literal["api", "recreate", "noop"]
    new_index_id: str | None = None
    detail: str = ""


class IndexesResource(Resource[Index]):
    """Indexes live under a KB: /control/knowledge-bases/{kb_id}/indexes/..."""

    model = Index

    def _path_for_kb(self, kb_id: str) -> str:
        return f"/control/knowledge-bases/{kb_id}/indexes"

    # ---- CRUD ----------------------------------------------------------------
    def list(  # type: ignore[override]
        self, kb_id: str, *, limit: int | None = None, after: str | None = None
    ) -> ListResponse[Index]:
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if after is not None:
            params["after"] = after
        raw = self._get_json(self._path_for_kb(kb_id), params=params or None)
        return ListResponse[Index].model_validate(raw)

    def get(self, kb_id: str, index_id: str) -> Index:  # type: ignore[override]
        raw = self._get_json(f"{self._path_for_kb(kb_id)}/{index_id}")
        return Index.model_validate(raw)

    def create(self, kb_id: str, payload: IndexCreate) -> Index:
        raw = self._post_json(
            self._path_for_kb(kb_id), json=payload.model_dump(mode="json", exclude_none=True)
        )
        return Index.model_validate(raw)

    def update(self, kb_id: str, index_id: str, payload: IndexUpdate) -> Index:
        raw = self._post_json(
            f"{self._path_for_kb(kb_id)}/{index_id}",
            json=payload.model_dump(mode="json", exclude_none=True),
        )
        return Index.model_validate(raw)

    def delete(self, kb_id: str, index_id: str) -> None:  # type: ignore[override]
        """DELETE an index. The endpoint isn't in the published Broadcom doc; some
        deployments expose it, others 404/405. Probe-then-fallback: on a missing
        endpoint, raise `IndexDeleteUnsupported` with actionable alternatives so
        callers (CLI cleanup workflow) can guide the user."""
        try:
            self._delete(f"{self._path_for_kb(kb_id)}/{index_id}")
        except (PaisNotFoundError, PaisError) as e:
            if _is_endpoint_missing(e):
                raise IndexDeleteUnsupported(
                    status_code=getattr(e, "status_code", None),
                    request_id=getattr(e, "request_id", None),
                ) from e
            raise

    # ---- Documents -----------------------------------------------------------
    def list_documents(
        self,
        kb_id: str,
        index_id: str,
        *,
        limit: int | None = None,
        after: str | None = None,
    ) -> ListResponse[Document]:
        """Single-page list. Honors cursor pagination via `limit` + `after`.

        Most callers should prefer `iter_documents()` which transparently
        follows `has_more` / `last_id`. This single-page method is kept for
        thin wire-level checks.
        """
        params: dict[str, Any] = {}
        if limit is not None:
            params["limit"] = limit
        if after is not None:
            params["after"] = after
        raw = self._get_json(
            f"{self._path_for_kb(kb_id)}/{index_id}/documents",
            params=params or None,
        )
        return ListResponse[Document].model_validate(raw)

    def iter_documents(
        self,
        kb_id: str,
        index_id: str,
        *,
        limit: int = 100,
        max_pages: int = 1000,
    ) -> Iterator[Document]:
        """Iterate every document in an index, following `has_more` + `last_id`.

        Mirrors `Resource.list_all` but for the KB-scoped documents endpoint.
        `max_pages` is a hard cap defending against a server that mis-reports
        `has_more=True` forever — raises RuntimeError if hit.
        """
        after: str | None = None
        for _ in range(max_pages):
            page = self.list_documents(kb_id, index_id, limit=limit, after=after)
            yield from page.data
            if not page.has_more:
                return
            if page.last_id:
                after = page.last_id
                continue
            if page.data:
                last_doc = page.data[-1]
                after = getattr(last_doc, "id", None)
                if after is None:
                    return
                continue
            return
        raise RuntimeError(
            f"iter_documents exceeded max_pages={max_pages} for index {index_id}; "
            "server may be mis-reporting has_more"
        )

    def delete_document(self, kb_id: str, index_id: str, document_id: str) -> None:
        """DELETE a single document by id. Not in the public PAIS docs but most
        deployments expose it; callers should be ready for 404/405 fallback."""
        self._delete(f"{self._path_for_kb(kb_id)}/{index_id}/documents/{document_id}")

    def upload_document(
        self,
        kb_id: str,
        index_id: str,
        file_path: str | Path,
        *,
        content_type: str = "application/octet-stream",
    ) -> Document:
        path = Path(file_path)
        with path.open("rb") as fh:
            files: dict[str, tuple[str, IO[bytes], str]] = {"file": (path.name, fh, content_type)}
            resp = self._transport.request(
                "POST",
                f"{self._path_for_kb(kb_id)}/{index_id}/documents",
                files=files,
            )
        return Document.model_validate(resp.body)

    # ---- Indexing ------------------------------------------------------------
    def trigger_indexing(self, kb_id: str, index_id: str) -> Indexing:
        raw = self._post_json(f"{self._path_for_kb(kb_id)}/{index_id}/indexings", json={})
        return Indexing.model_validate(raw)

    def get_active_indexing(self, kb_id: str, index_id: str) -> Indexing | None:
        from pais.errors import PaisNotFoundError

        try:
            raw = self._get_json(f"{self._path_for_kb(kb_id)}/{index_id}/active-indexing")
        except PaisNotFoundError:
            return None
        if raw is None:
            return None
        return Indexing.model_validate(raw)

    def wait_for_indexing(
        self,
        kb_id: str,
        index_id: str,
        *,
        timeout: float = 300.0,
        interval: float = 2.0,
        max_interval: float = 10.0,
        sleep: Any = time.sleep,
    ) -> Indexing:
        """Poll `active-indexing` until DONE/FAILED/CANCELLED or timeout.

        Returns the final Indexing. Raises TimeoutError on deadline.
        """
        deadline = time.monotonic() + timeout
        current_interval = interval
        last: Indexing | None = None
        while time.monotonic() < deadline:
            indexing = self.get_active_indexing(kb_id, index_id)
            if indexing is None:
                if last is not None:
                    return last  # active job cleared → prior state is terminal
                sleep(current_interval)
                current_interval = min(current_interval * 1.2, max_interval)
                continue
            last = indexing
            if indexing.state in _TERMINAL_STATES:
                return indexing
            sleep(current_interval)
            current_interval = min(current_interval * 1.2, max_interval)
        raise TimeoutError(f"Indexing did not finish within {timeout}s")

    # ---- Search --------------------------------------------------------------
    def search(self, kb_id: str, index_id: str, query: SearchQuery) -> SearchResponse:
        # `by_alias=True` produces the doc-aligned wire body
        # `{text, top_k, similarity_cutoff}` (Python field names stay query/top_n).
        raw = self._post_json(
            f"{self._path_for_kb(kb_id)}/{index_id}/search",
            json=query.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        # Some PAIS builds return a bare list of hits; normalize.
        if isinstance(raw, list):
            return SearchResponse(hits=[SearchHit.model_validate(h) for h in raw])
        return SearchResponse.model_validate(raw)

    # ---- Cleanup -------------------------------------------------------------
    def purge(
        self,
        kb_id: str,
        index_id: str,
        *,
        strategy: CleanupStrategy = "auto",
        match_origin_prefix: str | None = None,
    ) -> PurgeResult:
        """Delete documents from an index.

        - ``api`` — DELETE each `/documents/{id}` (fails fast if PAIS lacks the endpoint).
        - ``recreate`` — delete the index and recreate with the same config.
          Only honored when ``match_origin_prefix`` is None (otherwise refused
          since recreate would also drop unrelated docs).
        - ``auto`` — try ``api`` first; on 404/405 fall back to ``recreate``
          (only when ``match_origin_prefix`` is None).

        ``match_origin_prefix`` filters documents whose ``origin_name`` starts
        with the prefix. Used by ``--replace`` to only purge re-uploaded suites.
        """
        if strategy == "recreate":
            if match_origin_prefix is not None:
                raise ValueError(
                    "strategy='recreate' deletes the entire index and cannot be "
                    "combined with match_origin_prefix"
                )
            return self._purge_recreate(kb_id, index_id)

        # Collect every matching doc id up front — paginating the listing — then
        # delete. Deleting while iterating would invalidate the server-side cursor
        # (the last_id we'd follow may be the doc we just deleted), so we snapshot
        # IDs first.
        targets: list[tuple[str, str]] = []
        for doc in self.iter_documents(kb_id, index_id):
            if match_origin_prefix is None or doc.origin_name.startswith(match_origin_prefix):
                targets.append((doc.id, doc.origin_name))

        if not targets:
            return PurgeResult(strategy_used="api", documents_deleted=0)

        deleted = 0
        errors: list[str] = []
        for i, (doc_id, _origin) in enumerate(targets):
            try:
                self.delete_document(kb_id, index_id, doc_id)
                deleted += 1
            except (PaisNotFoundError, PaisError) as e:
                # On the FIRST attempt, treat 404/405 as endpoint-missing and fall back.
                if i == 0 and strategy == "auto" and _is_endpoint_missing(e):
                    if match_origin_prefix is not None:
                        errors.append(
                            "PAIS lacks per-document DELETE; cannot purge by prefix. "
                            "Use --strategy recreate (drops the entire index) or upgrade PAIS."
                        )
                        return PurgeResult(strategy_used="api", documents_deleted=0, errors=errors)
                    _log.warning(
                        "pais.purge.fallback_recreate",
                        kb_id=kb_id,
                        index_id=index_id,
                        reason=str(e),
                    )
                    return self._purge_recreate(kb_id, index_id)
                if strategy == "api" and _is_endpoint_missing(e):
                    errors.append(
                        f"PAIS deployment does not expose DELETE /documents/{{id}} "
                        f"(error on first doc {doc_id}: {e})"
                    )
                    return PurgeResult(
                        strategy_used="api",
                        documents_deleted=deleted,
                        errors=errors,
                    )
                errors.append(f"{doc_id}: {type(e).__name__}: {e}")
        return PurgeResult(strategy_used="api", documents_deleted=deleted, errors=errors)

    def _purge_recreate(self, kb_id: str, index_id: str) -> PurgeResult:
        """Delete the index and recreate it with the same configuration.

        Raises `IndexDeleteUnsupported` (re-thrown with clearer context) if the
        deployment doesn't expose per-index DELETE — the recreate strategy is
        unavailable on those servers.
        """
        ix = self.get(kb_id, index_id)
        first_page = self.list_documents(kb_id, index_id)
        deleted_count = first_page.num_objects
        if deleted_count is None:
            # Server didn't report a total; walk every page to get the real count.
            deleted_count = sum(1 for _ in self.iter_documents(kb_id, index_id))
        try:
            self.delete(kb_id, index_id)
        except IndexDeleteUnsupported as e:
            raise IndexDeleteUnsupported(
                "recreate strategy unavailable: PAIS doesn't expose per-index "
                "DELETE on this deployment. Delete the parent KB to clear contents."
            ) from e
        new_payload = IndexCreate(
            name=ix.name,
            description=ix.description,
            embeddings_model_endpoint=ix.embeddings_model_endpoint,
            text_splitting=ix.text_splitting,
            chunk_size=ix.chunk_size,
            chunk_overlap=ix.chunk_overlap,
        )
        new_ix = self.create(kb_id, new_payload)
        _log.info(
            "pais.index.recreated",
            kb_id=kb_id,
            old_index_id=index_id,
            new_index_id=new_ix.id,
        )
        return PurgeResult(
            strategy_used="recreate",
            documents_deleted=deleted_count,
            new_index_id=new_ix.id,
        )

    # ---- Cancel indexing -----------------------------------------------------
    def cancel_indexing(
        self,
        kb_id: str,
        index_id: str,
        *,
        strategy: CleanupStrategy = "auto",
    ) -> CancelResult:
        """Stop an in-progress indexing job.

        - ``api`` — DELETE /active-indexing (raises if endpoint missing).
        - ``recreate`` — delete + recreate the index (changes the index_id).
        - ``auto`` — try API, fall back to recreate on 404/405.
        """
        active = self.get_active_indexing(kb_id, index_id)
        if active is None or active.state in _TERMINAL_STATES:
            return CancelResult(
                cancelled=False,
                strategy_used="noop",
                detail="no active indexing — nothing to cancel",
            )

        if strategy in ("api", "auto"):
            try:
                self._delete(f"{self._path_for_kb(kb_id)}/{index_id}/active-indexing")
                return CancelResult(
                    cancelled=True, strategy_used="api", detail="DELETE active-indexing succeeded"
                )
            except (PaisNotFoundError, PaisError) as e:
                if not _is_endpoint_missing(e):
                    raise
                if strategy == "api":
                    raise NotImplementedError(
                        "PAIS deployment does not expose DELETE /active-indexing. "
                        "Use --strategy recreate to delete the index entirely."
                    ) from e
                _log.warning(
                    "pais.cancel.fallback_recreate",
                    kb_id=kb_id,
                    index_id=index_id,
                    reason=str(e),
                )

        # strategy == "recreate" or auto-fallback
        result = self._purge_recreate(kb_id, index_id)
        return CancelResult(
            cancelled=True,
            strategy_used="recreate",
            new_index_id=result.new_index_id,
            detail=(f"index recreated; old_index_id={index_id} new_index_id={result.new_index_id}"),
        )


def _is_endpoint_missing(err: Exception) -> bool:
    status = getattr(err, "status_code", None)
    return status in (404, 405)
