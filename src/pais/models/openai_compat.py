"""OpenAI-compatible endpoints (`/compatibility/openai/v1/{models,embeddings,chat/completions}`)."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pais.models.common import PaisModel


class ModelType(str, Enum):
    COMPLETIONS = "COMPLETIONS"
    EMBEDDINGS = "EMBEDDINGS"


class ModelEngine(str, Enum):
    VLLM = "VLLM"
    INFINITY = "INFINITY"
    OTHER = "OTHER"


class Model(PaisModel):
    id: str
    object: Literal["model"] = "model"
    created: int | None = None
    model_type: ModelType | None = None
    model_engine: ModelEngine | None = None
    owned_by: str | None = None


class EmbeddingRequest(PaisModel):
    model: str
    input: str | list[str]


class EmbeddingData(PaisModel):
    object: Literal["embedding"] = "embedding"
    index: int
    embedding: list[float]


class EmbeddingResponse(PaisModel):
    object: Literal["list"] = "list"
    model: str
    data: list[EmbeddingData]
