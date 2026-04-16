"""Passthrough splitter: 1 file → 1 chunk, body == file bytes."""

from __future__ import annotations

import mimetypes
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from pais.ingest.registry import register_splitter
from pais.ingest.splitters._base import SplitDoc, SplitterMeta, SplitterOptionsBase


class PassthroughOptions(SplitterOptionsBase):
    """No options — passthrough is intentionally minimal."""


@register_splitter
class PassthroughSplitter:
    """Upload each input file as-is. PAIS handles any further chunking."""

    kind: ClassVar[str] = "passthrough"
    options_model: ClassVar[type[PassthroughOptions]] = PassthroughOptions
    meta: ClassVar[SplitterMeta] = SplitterMeta(
        summary="Upload each file as-is; PAIS handles all splitting",
        input_type="any file (text, markdown, PDF, binary — anything PAIS accepts)",
        algorithm=(
            "1 file → 1 chunk. Body is the raw file bytes; media_type is guessed by "
            "extension (`mimetypes.guess_type`). PAIS server-side ingestion does the "
            "actual splitting per the index's chunk_size + chunk_overlap."
        ),
        chunk_size_unit="file",
        typical_chunk_size="= file size (one chunk per file)",
        token_char_hint=None,
        example_input="a PDF, an .md file, a .log file — anything you want PAIS to split",
        notes=(
            "Best when PAIS's server-side splitter does the right thing for your data.",
            "`group_key` is the full filename (so --replace matches THIS file exactly).",
            "Be careful with sensitive content — full file is uploaded verbatim.",
        ),
    )

    def __init__(self, options: PassthroughOptions) -> None:
        self._options = options

    def split(self, path: Path) -> Iterator[SplitDoc]:
        media_type, _ = mimetypes.guess_type(path.name)
        yield SplitDoc(
            origin_name=path.name,
            body=path.read_bytes(),
            media_type=media_type or "application/octet-stream",
            metadata={"source_path": str(path)},
        )

    def group_key(self, path: Path) -> str:
        # Use the full filename so `--replace` matches exactly this file's
        # uploaded doc (path.stem alone would false-match e.g. `b.txt` with
        # an existing `bar.txt`).
        return path.name
