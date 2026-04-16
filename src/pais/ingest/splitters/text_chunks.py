"""Plain-text chunker: character-based windowing with overlap."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from pydantic import Field, model_validator

from pais.ingest.registry import register_splitter
from pais.ingest.splitters._base import SplitDoc, SplitterMeta, SplitterOptionsBase


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
    meta: ClassVar[SplitterMeta] = SplitterMeta(
        summary="Sliding character window with configurable overlap",
        input_type="any UTF-8 text (logs, plain text, scraped pages)",
        algorithm=(
            "Slides a fixed-size character window over the file. Each chunk is "
            "`chunk_chars` characters; consecutive chunks overlap by `overlap_chars` "
            "so context isn't cut in the middle of a sentence."
        ),
        chunk_size_unit="chars",
        typical_chunk_size="1500 chars per chunk (≈ 375 tokens English) with 100-char overlap",
        token_char_hint=(
            "≈ 4 chars/token (English); this splitter measures in chars, "
            "not tokens. Set chunk_chars to about 4x your index.chunk_size."
        ),
        example_input="a server log, a scraped HTML body, a long .txt file",
        notes=(
            "Doesn't respect markdown / paragraph boundaries — use "
            "`markdown_headings` or `test_suite_md` for structured docs.",
            "`group_key` is the filename stem — used by --replace.",
        ),
    )

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
