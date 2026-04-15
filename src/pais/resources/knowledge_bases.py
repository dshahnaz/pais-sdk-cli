"""Knowledge Bases resource."""

from __future__ import annotations

from pais.models.knowledge_base import (
    KnowledgeBase,
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
)
from pais.resources._base import Resource


class KnowledgeBasesResource(Resource[KnowledgeBase]):
    path = "/control/knowledge-bases"
    model = KnowledgeBase

    def create(self, payload: KnowledgeBaseCreate) -> KnowledgeBase:
        return self._create(payload)

    def update(self, kb_id: str, payload: KnowledgeBaseUpdate) -> KnowledgeBase:
        return self._update(kb_id, payload)
