"""Minimal heading-aware markdown parser.

Handles the test-suite shape: one H1 title, H2 section containers, H3
test-case sections. Ignores headings inside fenced code blocks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_H1_RE = re.compile(r"^#\s+(.+?)\s*$")
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_H3_RE = re.compile(r"^###\s+(.+?)\s*$")


@dataclass
class Heading:
    level: int  # 1, 2, 3
    title: str
    # Body lines that belong to this heading (until the next heading of
    # equal or higher level).
    body_lines: list[str] = field(default_factory=list)
    # Nested subsections (only populated for H2 with H3 children).
    children: list[Heading] = field(default_factory=list)


@dataclass
class ParsedDoc:
    title: str  # from H1
    sections: list[Heading]  # H2-level sections in source order


def parse(text: str) -> ParsedDoc:
    """Parse markdown into H1 title + H2 sections (with H3 children)."""
    title = ""
    sections: list[Heading] = []
    current_h2: Heading | None = None
    current_h3: Heading | None = None
    in_fence = False

    for raw in text.splitlines():
        if _FENCE_RE.match(raw):
            in_fence = not in_fence
            _append(current_h2, current_h3, raw)
            continue
        if in_fence:
            _append(current_h2, current_h3, raw)
            continue

        if (m := _H1_RE.match(raw)) and not title:
            title = m.group(1).strip()
            continue
        if m := _H2_RE.match(raw):
            current_h2 = Heading(level=2, title=m.group(1).strip())
            current_h3 = None
            sections.append(current_h2)
            continue
        if m := _H3_RE.match(raw):
            if current_h2 is None:
                # Orphan H3 — treat as its own H2 for robustness.
                current_h2 = Heading(level=2, title="")
                sections.append(current_h2)
            current_h3 = Heading(level=3, title=m.group(1).strip())
            current_h2.children.append(current_h3)
            continue

        _append(current_h2, current_h3, raw)

    return ParsedDoc(title=title, sections=sections)


def _append(h2: Heading | None, h3: Heading | None, line: str) -> None:
    if h3 is not None:
        h3.body_lines.append(line)
    elif h2 is not None:
        h2.body_lines.append(line)


def render_body(heading: Heading) -> str:
    """Render a heading's body lines back into a string, trimmed."""
    return "\n".join(heading.body_lines).strip()
