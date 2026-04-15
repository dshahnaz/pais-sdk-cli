"""Indexes, indexings, documents, and search (`.../knowledge-bases/{kb}/indexes/...`)."""

from __future__ import annotations

from enum import Enum
from typing import Literal

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
    text_splitting: TextSplittingKind = TextSplittingKind.SENTENCE
    chunk_size: int = 400
    chunk_overlap: int = 100
    status: IndexStatus = IndexStatus.AVAILABLE


class IndexCreate(PaisModel):
    name: str
    description: str | None = None
    embeddings_model_endpoint: str
    text_splitting: TextSplittingKind = TextSplittingKind.SENTENCE
    chunk_size: int = 400
    chunk_overlap: int = 100


class IndexUpdate(PaisModel):
    name: str | None = None
    description: str | None = None
    embeddings_model_endpoint: str | None = None
    text_splitting: TextSplittingKind | None = None
    chunk_size: int | None = None
    chunk_overlap: int | None = None


class Indexing(PaisModel):
    id: str
    object: Literal["indexing"] = "indexing"
    created_at: int
    index_id: str | None = None
    state: IndexingState = IndexingState.PENDING
    started_at: int | None = None
    finished_at: int | None = None
    error: str | None = None


class Document(PaisModel):
    id: str
    object: Literal["document"] = "document"
    created_at: int
    index_id: str | None = None
    origin_name: str
    state: DocumentState = DocumentState.PENDING
    size_bytes: int | None = None
    chunk_count: int | None = None


class SearchQuery(PaisModel):
    query: str
    top_n: int = 5
    similarity_cutoff: float = 0.0


class SearchHit(PaisModel):
    document_id: str
    chunk_id: str
    text: str
    score: float
    origin_name: str | None = None


class SearchResponse(PaisModel):
    object: Literal["search_result"] = "search_result"
    hits: list[SearchHit] = []
