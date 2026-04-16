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
from pais.ingest.splitters._base import SplitDoc, SplitterOptionsBase


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
