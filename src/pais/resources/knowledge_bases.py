"""Knowledge Bases resource."""

from __future__ import annotations

from dataclasses import dataclass, field

from pais.models.knowledge_base import (
    KnowledgeBase,
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
)
from pais.resources._base import Resource
from pais.resources.indexes import CleanupStrategy, IndexesResource, PurgeResult


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

    def purge(self, kb_id: str, *, strategy: CleanupStrategy = "auto") -> KbPurgeResult:
        """Delete every document from every index under the KB. KB itself is kept."""
        indexes = IndexesResource(self._transport)
        result = KbPurgeResult()
        for ix in indexes.list(kb_id).data:
            try:
                pr = indexes.purge(kb_id, ix.id, strategy=strategy)
                result.per_index.append(pr)
                result.documents_deleted += pr.documents_deleted
                result.indexes_processed += 1
            except Exception as e:
                result.errors.append(f"{ix.id}: {type(e).__name__}: {e}")
        return result
