"""Indexes, indexings, documents, and search (`.../knowledge-bases/{kb}/indexes/...`)."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import Field, model_validator

from pais.models.common import PaisModel


class IndexStatus(str, Enum):
    CREATING = "CREATING"
    AVAILABLE = "AVAILABLE"
    FAILED = "FAILED"
    DELETING = "DELETING"


class TextSplittingKind(str, Enum):
    SENTENCE = "SENTENCE"
    PARAGRAPH = "PARAGRAPH"
    FIXED = "FIXED"


class IndexingState(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class DocumentState(str, Enum):
    PENDING = "PENDING"
    INDEXING = "INDEXING"
    INDEXED = "INDEXED"
    FAILED = "FAILED"


class Index(PaisModel):
    id: str
    object: Literal["index"] = "index"
    created_at: int
    kb_id: str | None = None
    name: str
    description: str | None = None
    embeddings_model_endpoint: str
    text_splitting: str = "SENTENCE"
    chunk_size: int = 400
    chunk_overlap: int = 100
    status: str = "AVAILABLE"


class IndexCreate(PaisModel):
    name: str
    description: str | None = None
    embeddings_model_endpoint: str
    text_splitting: str = "SENTENCE"
    chunk_size: int = 400
    chunk_overlap: int = 100


class IndexUpdate(PaisModel):
    name: str | None = None
    description: str | None = None
    embeddings_model_endpoint: str | None = None
    text_splitting: str | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None


class Indexing(PaisModel):
    id: str
    object: Literal["indexing"] = "indexing"
    created_at: int
    index_id: str | None = None
    state: str = "PENDING"
    started_at: int | None = None
    finished_at: int | None = None
    error: str | None = None


class Document(PaisModel):
    id: str
    object: Literal["document"] = "document"
    created_at: int
    index_id: str | None = None
    origin_name: str
    state: str = "PENDING"
    size_bytes: int | None = None
    chunk_count: int | None = None


class SearchQuery(PaisModel):
    """Search request body. Doc-aligned wire format: `{text, top_k, similarity_cutoff}`.

    Python field names stay `query` / `top_n` for back-compat; the JSON wire
    payload uses `serialization_alias` so the request body matches the
    Broadcom doc verbatim. `populate_by_name=True` (inherited from PaisModel)
    means callers can also construct from `{text, top_k}`.
    """

    query: str = Field(..., serialization_alias="text")
    top_n: int = Field(default=5, serialization_alias="top_k")
    similarity_cutoff: float = 0.0


class SearchHit(PaisModel):
    """One result chunk. Doc-aligned fields:
    `{origin_name, origin_ref, document_id, score, media_type, text}`.
    `chunk_id` is kept as optional for back-compat with older PAIS deployments
    that returned it under the legacy `{hits: [...]}` shape."""

    document_id: str
    text: str
    score: float
    origin_name: str | None = None
    origin_ref: str | None = None
    media_type: str | None = None
    chunk_id: str | None = None  # legacy; absent in the doc-aligned response


class SearchResponse(PaisModel):
    """Search response. Accepts both the doc-aligned `{chunks: [...]}` shape
    and the legacy `{hits: [...]}` shape; both expose `.hits` to callers."""

    object: Literal["search_result"] = "search_result"
    hits: list[SearchHit] = []

    @model_validator(mode="before")
    @classmethod
    def _normalize_chunks_to_hits(cls, data: Any) -> Any:
        """Map the doc-aligned `chunks` key to our `hits` field. Tolerates:
        - {"chunks": [...]} (doc shape)
        - {"hits": [...]} (legacy shape)
        - bare list (very old PAIS) — already handled in IndexesResource.search.
        """
        if not isinstance(data, dict):
            return data
        if "chunks" in data and "hits" not in data:
            data = {**data, "hits": data["chunks"]}
        return data
