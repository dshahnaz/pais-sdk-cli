"""Plain-text chunker: character-based windowing with overlap."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from pydantic import Field, model_validator

from pais.ingest.registry import register_splitter
from pais.ingest.splitters._base import SplitDoc, SplitterOptionsBase


class TextChunksOptions(SplitterOptionsBase):
    chunk_chars: int = Field(default=1500, ge=100, description="Target chars per chunk.")
    overlap_chars: int = Field(
        default=100, ge=0, description="Char overlap between adjacent chunks."
    )

    @model_validator(mode="after")
    def _check(self) -> TextChunksOptions:
        if self.overlap_chars >= self.chunk_chars:
            raise ValueError("overlap_chars must be smaller than chunk_chars")
        return self


@register_splitter
class TextChunksSplitter:
    """Slide a fixed-size window over the file's text. Useful for plain text / logs."""

    kind: ClassVar[str] = "text_chunks"
    options_model: ClassVar[type[TextChunksOptions]] = TextChunksOptions

    def __init__(self, options: TextChunksOptions) -> None:
        self._opts = options

    def split(self, path: Path) -> Iterator[SplitDoc]:
        text = path.read_text(encoding="utf-8", errors="replace")
        stem = path.stem
        size = self._opts.chunk_chars
        overlap = self._opts.overlap_chars
        step = size - overlap
        if not text:
            return
        idx = 0
        part = 0
        while idx < len(text):
            part += 1
            chunk = text[idx : idx + size]
            yield SplitDoc(
                origin_name=f"{stem}__part{part:03d}.txt",
                body=chunk.encode("utf-8"),
                media_type="text/plain",
                metadata={"part": part, "char_offset": idx},
            )
            idx += step

    def group_key(self, path: Path) -> str:
        return f"{path.stem}__"
