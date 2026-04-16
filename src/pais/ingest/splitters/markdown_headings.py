"""Generic markdown splitter: split at a configurable heading level."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from pydantic import Field

from pais.dev.markdown import parse, render_body
from pais.dev.split_suite import _slugify
from pais.ingest.registry import register_splitter
from pais.ingest.splitters._base import SplitDoc, SplitterOptionsBase


class MarkdownHeadingsOptions(SplitterOptionsBase):
    heading_level: int = Field(default=2, ge=2, le=3, description="Split at H2 (2) or H3 (3).")
    breadcrumb: bool = Field(
        default=True,
        description="Prepend a `# Doc: <H1>\\n## Section: <heading>` header to each chunk.",
    )


@register_splitter
class MarkdownHeadingsSplitter:
    """Split any markdown file at the chosen heading level. No suite assumptions."""

    kind: ClassVar[str] = "markdown_headings"
    options_model: ClassVar[type[MarkdownHeadingsOptions]] = MarkdownHeadingsOptions

    def __init__(self, options: MarkdownHeadingsOptions) -> None:
        self._opts = options

    def split(self, path: Path) -> Iterator[SplitDoc]:
        text = path.read_text(encoding="utf-8")
        doc = parse(text)
        title = (doc.title or path.stem).strip()
        title_slug = _slugify(title) or path.stem

        if self._opts.heading_level == 2:
            for i, h2 in enumerate(doc.sections, start=1):
                body = render_body(h2)
                if not body and not h2.children:
                    continue
                # If H2 has H3 children, include them inline.
                if h2.children:
                    parts: list[str] = [body] if body else []
                    for h3 in h2.children:
                        parts.append(f"### {h3.title}\n\n{render_body(h3)}".rstrip())
                    body = "\n\n".join(p for p in parts if p)
                yield self._render(
                    title=title,
                    title_slug=title_slug,
                    section=h2.title or f"section_{i}",
                    body=body,
                    order=i,
                )
        else:  # heading_level == 3
            n = 0
            for h2 in doc.sections:
                for h3 in h2.children:
                    n += 1
                    body = render_body(h3)
                    if not body:
                        continue
                    section = f"{h2.title} / {h3.title}".strip(" /")
                    yield self._render(
                        title=title, title_slug=title_slug, section=section, body=body, order=n
                    )

    def _render(
        self, *, title: str, title_slug: str, section: str, body: str, order: int
    ) -> SplitDoc:
        section_slug = _slugify(section) or f"section_{order}"
        if self._opts.breadcrumb:
            rendered = f"# Doc: {title}\n## Section: {section}\n\n{body.strip()}\n"
        else:
            rendered = body.strip() + "\n"
        return SplitDoc(
            origin_name=f"{title_slug}__{order:02d}__{section_slug}.md",
            body=rendered.encode("utf-8"),
            media_type="text/markdown",
            metadata={"doc_title": title, "section": section, "order": order},
        )

    def group_key(self, path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        doc = parse(text)
        slug = _slugify(doc.title or path.stem) or path.stem
        return f"{slug}__"
