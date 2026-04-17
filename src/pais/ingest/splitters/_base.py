"""Splitter protocol + the upload-ready record it yields."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel


@dataclass
class SplitDoc:
    """One upload-ready chunk produced by a splitter."""

    origin_name: str
    body: bytes
    media_type: str = "text/markdown"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SplitterMeta:
    """Human-readable metadata every splitter declares.

    Surfaced by `pais splitters list`/`show`, the interactive picker, and
    workflow B's pre-ingest brief — so users pick the right splitter without
    reading source.

    The `target_embeddings_model` / `suggested_index_chunk_size` /
    `suggested_index_chunk_overlap` fields let a splitter declare the index
    config it was tuned for. The CLI renders them in `splitters show` and
    uses them for pre-flight checks in `pais ingest run` and `pais kb ensure`
    (mismatch between splitter target and index config → warning).
    """

    summary: str  # one-line tagline (≤ 70 chars)
    input_type: str  # e.g. "structured markdown (H1/H2/H3)" / "any UTF-8 text"
    algorithm: str  # 1-3 sentences, plain English
    chunk_size_unit: str  # "tokens" | "chars" | "file"
    typical_chunk_size: str  # e.g. "≈ 400 tokens (~1.5 KB English)"
    token_char_hint: str | None  # e.g. "≈ 4 chars/token (English, BAAI/bge-small-en-v1.5)"
    example_input: str  # one-line example
    notes: tuple[str, ...] = ()  # caveats, limits
    target_embeddings_model: str | None = None  # e.g. "BAAI/bge-small-en-v1.5"
    suggested_index_chunk_size: int | None = None  # tokens
    suggested_index_chunk_overlap: int | None = None  # tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "input_type": self.input_type,
            "algorithm": self.algorithm,
            "chunk_size_unit": self.chunk_size_unit,
            "typical_chunk_size": self.typical_chunk_size,
            "token_char_hint": self.token_char_hint,
            "example_input": self.example_input,
            "notes": list(self.notes),
            "target_embeddings_model": self.target_embeddings_model,
            "suggested_index_chunk_size": self.suggested_index_chunk_size,
            "suggested_index_chunk_overlap": self.suggested_index_chunk_overlap,
        }


_DEFAULT_META = SplitterMeta(
    summary="(no summary — splitter is missing `meta`)",
    input_type="(unknown)",
    algorithm="(no description provided)",
    chunk_size_unit="(unknown)",
    typical_chunk_size="(unknown)",
    token_char_hint=None,
    example_input="(none)",
)


def meta_for(cls: type) -> SplitterMeta:
    """Return the splitter class's `meta`, or a placeholder if it predates v0.6.1.

    The Protocol marks `meta` as required, but we don't enforce it at register
    time — third-party splitters built against the v0.6 contract would crash
    otherwise. The placeholder lets the CLI render gracefully while signalling
    that the splitter author should add metadata.
    """
    return getattr(cls, "meta", _DEFAULT_META)


@runtime_checkable
class Splitter(Protocol):
    """Per-index splitter contract.

    Implementations declare a `kind` (registry key), an `options_model`
    (pydantic schema for the TOML `[splitter]` block), and a `meta`
    (`SplitterMeta` — human-readable docs surfaced by the CLI).
    Construction takes the parsed options instance.
    """

    kind: ClassVar[str]
    options_model: ClassVar[type[BaseModel]]
    meta: ClassVar[SplitterMeta]

    def __init__(self, options: BaseModel) -> None: ...

    def split(self, path: Path) -> Iterator[SplitDoc]:
        """Yield one SplitDoc per output chunk for the given input file."""
        ...

    def group_key(self, path: Path) -> str:
        """Return the slug used by --replace to match origin_name prefixes."""
        ...


class SplitterOptionsBase(BaseModel):
    """Base for splitter option models. Subclasses add fields freely."""

    model_config = {"extra": "forbid"}
