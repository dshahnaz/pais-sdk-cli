"""Split one test-suite markdown file into per-section files sized for PAIS.

Each emitted file is <= 400 tokens (measured with bge-small-en-v1.5),
starts with a breadcrumb header, and has a filename encoding suite + order
+ kind + section-name.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pais.dev.markdown import parse, render_body
from pais.dev.token_budget import BUDGET, token_count

Kind = Literal["overview", "test", "tech_stack"]

_ORDER_BY_KIND: dict[Kind, int] = {"overview": 5, "test": 10, "tech_stack": 20}

_OVERVIEW_H2_TITLES = frozenset({"overview", "deployment information", "components"})
_TEST_CONTAINER_TITLES = frozenset({"test coverage"})
_TECH_STACK_TITLES = frozenset({"technology stack", "tech stack"})

_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]+")
_PARA_SPLIT_RE = re.compile(r"\n\s*\n")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


class SectionTooLargeError(ValueError):
    """A single indivisible unit (e.g. one sentence) exceeds the token budget."""


@dataclass
class SplitSection:
    suite_name: str
    section_name: str
    kind: Kind
    order: int
    part: int | None
    body: str
    filename: str
    rendered: str


def split_suite(md_path: str | Path) -> list[SplitSection]:
    """Split one suite markdown file into budget-sized SplitSection entries."""
    path = Path(md_path)
    doc = parse(path.read_text(encoding="utf-8"))
    if not doc.title:
        raise ValueError(f"{path}: no H1 title found; expected '# SuiteName'")
    suite = doc.title.strip()
    suite_slug = _slugify(suite)

    atoms: list[tuple[Kind, str, str]] = []  # (kind, section_name, body_text)
    overview_parts: list[str] = []

    for h2 in doc.sections:
        h2_key = h2.title.strip().lower()
        if h2_key in _OVERVIEW_H2_TITLES:
            body = render_body(h2)
            if body:
                overview_parts.append(f"## {h2.title}\n\n{body}")
            continue
        if h2_key in _TEST_CONTAINER_TITLES:
            for h3 in h2.children:
                body = render_body(h3)
                atoms.append(("test", h3.title.strip(), body))
            continue
        if h2_key in _TECH_STACK_TITLES:
            body = render_body(h2)
            if body:
                atoms.append(("tech_stack", "tech_stack", body))
            continue
        # Unknown H2 — group under its own atom so content isn't dropped.
        body = render_body(h2)
        if body:
            atoms.append(("overview", _slugify(h2.title or "section"), body))

    if overview_parts:
        atoms.insert(0, ("overview", "overview", "\n\n".join(overview_parts)))

    out: list[SplitSection] = []
    for kind, section_name, body in atoms:
        out.extend(
            _emit(
                suite=suite, suite_slug=suite_slug, kind=kind, section_name=section_name, body=body
            )
        )
    return out


def write_sections(sections: Iterable[SplitSection], out_dir: str | Path) -> list[Path]:
    """Write rendered sections to `out_dir`. Returns the written paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for s in sections:
        p = out / s.filename
        p.write_text(s.rendered, encoding="utf-8")
        written.append(p)
    return written


# -------------------- internals --------------------


def _emit(
    *,
    suite: str,
    suite_slug: str,
    kind: Kind,
    section_name: str,
    body: str,
) -> list[SplitSection]:
    bodies = _fit_to_budget(suite=suite, kind=kind, section_name=section_name, body=body)
    order = _ORDER_BY_KIND[kind]
    slug = _slugify(section_name) or f"section_{order}"
    sections: list[SplitSection] = []
    total = len(bodies)
    for i, part_body in enumerate(bodies, start=1):
        part: int | None = i if total > 1 else None
        rendered = _render_file(suite=suite, section_name=section_name, kind=kind, body=part_body)
        # Final safety net: tokenized render must be under budget.
        n = token_count(rendered)
        if n > BUDGET:
            raise SectionTooLargeError(
                f"{suite}:{section_name} part {i}/{total} is {n} tokens (> {BUDGET})"
            )
        filename = _build_filename(suite_slug, order, kind, slug, part)
        sections.append(
            SplitSection(
                suite_name=suite,
                section_name=section_name,
                kind=kind,
                order=order,
                part=part,
                body=part_body,
                filename=filename,
                rendered=rendered,
            )
        )
    return sections


def _render_file(*, suite: str, section_name: str, kind: Kind, body: str) -> str:
    return f"# Suite: {suite}\n## Section: {section_name}\n## Kind: {kind}\n\n{body.strip()}\n"


def _build_filename(
    suite_slug: str, order: int, kind: Kind, section_slug: str, part: int | None
) -> str:
    base = f"{suite_slug}__{order:02d}_{kind}__{section_slug}"
    if part is not None:
        base = f"{base}__part{part}"
    return f"{base}.md"


def _slugify(text: str) -> str:
    s = _SLUG_RE.sub("_", text).strip("_")
    return s or ""


def _fit_to_budget(*, suite: str, kind: Kind, section_name: str, body: str) -> list[str]:
    """Return a list of body fragments each fitting under BUDGET when rendered."""
    if _fits(suite=suite, kind=kind, section_name=section_name, body=body):
        return [body]

    # Try paragraph splitting.
    paragraphs = [p.strip() for p in _PARA_SPLIT_RE.split(body) if p.strip()]
    if len(paragraphs) > 1:
        groups = _group_fragments(
            paragraphs, suite=suite, kind=kind, section_name=section_name, joiner="\n\n"
        )
        if groups is not None:
            return groups

    # Fall back to sentence splitting.
    sentences = [s.strip() for s in _SENT_SPLIT_RE.split(body) if s.strip()]
    if len(sentences) > 1:
        groups = _group_fragments(
            sentences, suite=suite, kind=kind, section_name=section_name, joiner=" "
        )
        if groups is not None:
            return groups

    # Cannot split further — single indivisible unit too large.
    raise SectionTooLargeError(
        f"{suite}:{section_name}: body too large to split and exceeds {BUDGET} tokens"
    )


def _group_fragments(
    fragments: list[str],
    *,
    suite: str,
    kind: Kind,
    section_name: str,
    joiner: str,
) -> list[str] | None:
    """Greedy-pack fragments into groups each fitting the budget. None if any single fragment is already too large."""
    groups: list[str] = []
    current: list[str] = []

    def current_body() -> str:
        return joiner.join(current)

    for frag in fragments:
        # Check if frag alone overflows — if so, return None so caller tries finer split.
        if not _fits(suite=suite, kind=kind, section_name=section_name, body=frag):
            return None
        candidate_body = joiner.join([*current, frag]) if current else frag
        if _fits(suite=suite, kind=kind, section_name=section_name, body=candidate_body):
            current.append(frag)
        else:
            groups.append(current_body())
            current = [frag]
    if current:
        groups.append(current_body())
    return groups


def _fits(*, suite: str, kind: Kind, section_name: str, body: str) -> bool:
    rendered = _render_file(suite=suite, section_name=section_name, kind=kind, body=body)
    return token_count(rendered) <= BUDGET
