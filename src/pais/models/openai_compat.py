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
    """A model routing entry. `model_type` and `model_engine` are typed as
    `str` (not Enum) because the doc treats them as free-form strings and
    real deployments return values beyond the documented set (e.g. `LLAMA_CPP`).
    Use `ModelType.COMPLETIONS` / `ModelEngine.VLLM` etc. as named constants
    for comparison — they're str-Enums so equality works both ways."""

    id: str
    object: Literal["model"] = "model"
    created: int | None = None
    model_type: str | None = None
    model_engine: str | None = None
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
