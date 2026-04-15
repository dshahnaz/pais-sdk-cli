"""Indexes + indexings + documents + search (nested under /knowledge-bases/{kb_id})."""

from __future__ import annotations

import time
from pathlib import Path
from typing import IO, Any

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

_TERMINAL_STATES = {IndexingState.DONE, IndexingState.FAILED, IndexingState.CANCELLED}


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
        self._delete(f"{self._path_for_kb(kb_id)}/{index_id}")

    # ---- Documents -----------------------------------------------------------
    def list_documents(self, kb_id: str, index_id: str) -> ListResponse[Document]:
        raw = self._get_json(f"{self._path_for_kb(kb_id)}/{index_id}/documents")
        return ListResponse[Document].model_validate(raw)

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
        raw = self._post_json(
            f"{self._path_for_kb(kb_id)}/{index_id}/search",
            json=query.model_dump(mode="json", exclude_none=True),
        )
        # Some PAIS builds return bare list of hits; normalize.
        if isinstance(raw, list):
            return SearchResponse(hits=[SearchHit.model_validate(h) for h in raw])
        return SearchResponse.model_validate(raw)
