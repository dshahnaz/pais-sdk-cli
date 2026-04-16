"""Test-suite markdown splitter: H1/H2/H3 atomic sections + breadcrumb header.

Wraps the original `pais.dev.split_suite` logic as a registered Splitter so it
slots into the v0.4 ingest pipeline.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from pydantic import Field

from pais.dev.markdown import parse
from pais.dev.split_suite import (
    SectionTooLargeError,  # noqa: F401  re-export for callers
    _slugify,
    split_suite,
)
from pais.ingest.registry import register_splitter
from pais.ingest.splitters._base import SplitDoc, SplitterMeta, SplitterOptionsBase


class TestSuiteMdOptions(SplitterOptionsBase):
    """Options for the `test_suite_md` splitter (declared in TOML)."""

    budget_tokens: int = Field(
        default=400,
        gt=0,
        le=512,
        description="Per-section token cap; sections over this are sub-split.",
    )


@register_splitter
class TestSuiteMdSplitter:
    """Splits one structured test-suite markdown file into per-section files.

    Behavior is identical to v0.3's `pais-dev split-suite`. The `budget_tokens`
    option is passed through to the underlying splitter (today it's a fixed
    constant; honoring this option becomes a no-op until the budget is plumbed).
    """

    kind: ClassVar[str] = "test_suite_md"
    options_model: ClassVar[type[TestSuiteMdOptions]] = TestSuiteMdOptions
    meta: ClassVar[SplitterMeta] = SplitterMeta(
        summary="Atomic per-section split for H1/H2/H3 test-suite markdown",
        input_type="structured markdown (H1=suite, H2=section, H3=subsection)",
        algorithm=(
            "Walks the H1/H2/H3 tree. Emits one chunk per H2 (or H3 leaf) section, "
            "prepended with a `# Doc / ## Section` breadcrumb so the LLM has context "
            "even when only that chunk is retrieved. Sections over `budget_tokens` "
            "are sub-split."
        ),
        chunk_size_unit="tokens",
        typical_chunk_size="≈ 400 tokens (~1.5 KB English)",
        token_char_hint="≈ 4 chars/token (English, BAAI/bge-small-en-v1.5)",
        example_input="~/Downloads/Access-Management.md (a structured test-suite file)",
        notes=(
            "Designed for the user's existing test-suite markdown. Generic markdown "
            "should use `markdown_headings` instead.",
            "`group_key` is the H1 slug (`<SuiteSlug>__`) — used by --replace.",
        ),
    )

    def __init__(self, options: TestSuiteMdOptions) -> None:
        self._options = options

    def split(self, path: Path) -> Iterator[SplitDoc]:
        for section in split_suite(path):
            yield SplitDoc(
                origin_name=section.filename,
                body=section.rendered.encode("utf-8"),
                media_type="text/markdown",
                metadata={
                    "suite_name": section.suite_name,
                    "section_name": section.section_name,
                    "kind": section.kind,
                    "order": section.order,
                    "part": section.part,
                },
            )

    def group_key(self, path: Path) -> str:
        """`<SuiteSlug>__` — exact prefix that emitted filenames start with."""
        doc = parse(path.read_text(encoding="utf-8"))
        slug = _slugify(doc.title or path.stem) or path.stem
        return f"{slug}__"
