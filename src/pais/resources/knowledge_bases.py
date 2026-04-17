"""Knowledge Bases resource."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any

from pais.models.knowledge_base import (
    KnowledgeBase,
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
)
from pais.resources._base import Resource
from pais.resources.indexes import CleanupStrategy, IndexesResource, ProgressCb, PurgeResult


@dataclass
class KbPurgeResult:
    documents_deleted: int = 0
    indexes_processed: int = 0
    per_index: list[PurgeResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class KnowledgeBasesResource(Resource[KnowledgeBase]):
    path = "/control/knowledge-bases"
    model = KnowledgeBase

    def create(self, payload: KnowledgeBaseCreate) -> KnowledgeBase:
        return self._create(payload)

    def update(self, kb_id: str, payload: KnowledgeBaseUpdate) -> KnowledgeBase:
        return self._update(kb_id, payload)

    def purge(
        self,
        kb_id: str,
        *,
        strategy: CleanupStrategy = "auto",
        on_progress: ProgressCb | None = None,
    ) -> KbPurgeResult:
        """Delete every document from every index under the KB. KB itself is kept.

        ``on_progress`` is an optional callback forwarded to each nested
        ``Indexes.purge`` call; this method also emits two KB-level events:

        * ``"index_start"`` — before each per-index purge.
          ``index_id=str, index_name=str, i=int, n=int``.
        * ``"index_done"`` — after each per-index purge succeeds.
          ``index_id=str, deleted=int``.

        A buggy callback cannot abort the purge (exceptions are swallowed).
        """

        def _emit(event: str, **payload: Any) -> None:
            if on_progress is None:
                return
            with contextlib.suppress(Exception):
                on_progress(event, **payload)

        indexes = IndexesResource(self._transport)
        result = KbPurgeResult()
        ix_list = list(indexes.list(kb_id).data)
        n = len(ix_list)
        for i, ix in enumerate(ix_list, start=1):
            _emit("index_start", index_id=ix.id, index_name=ix.name, i=i, n=n)
            try:
                pr = indexes.purge(kb_id, ix.id, strategy=strategy, on_progress=on_progress)
                result.per_index.append(pr)
                result.documents_deleted += pr.documents_deleted
                result.indexes_processed += 1
                _emit("index_done", index_id=ix.id, deleted=pr.documents_deleted)
            except Exception as e:
                result.errors.append(f"{ix.id}: {type(e).__name__}: {e}")
                _emit("index_done", index_id=ix.id, deleted=0)
        return result
