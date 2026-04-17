"""Shared parsing + chunking primitives for the test-suite splitters.

Parses one test-suite markdown file into a structured `SuiteDoc`, then provides
the helpers the concrete splitters (`test_suite_bge`, `test_suite_arctic`) use
to produce atomic, self-identifying `SplitDoc`s.

Expected input shape (the template all ~300 suites share):

    # <SuiteName>                        ← H1 (suite name)
    ## Overview                          ← H2 (suite context)
    ## Deployment Information            ← H2 (suite context, has `**Testbed Type**: ...`)
    ## Components                        ← H2 (suite context, bulleted `**Name** - desc`)
    ## Test Coverage                     ← H2 (container for test cases)
        ### testCaseName                 ← H3 (one per test case)
        **Purpose**: ...
        **Validations**: ...
        **Key Operations**: ...
        **API Endpoints**: ...
    ## Technology Stack                  ← H2 (suite context footer)

The splitter produces: 1 overview chunk (Overview + Deployment + Components +
Tech Stack) + 1 chunk per test case, each prepended with a compact breadcrumb
so the embedding vector captures suite-level context even when retrieved alone.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from pais.ingest.splitters._base import SplitDoc

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_H1_RE = re.compile(r"^#\s+(.+?)\s*$")
_H2_RE = re.compile(r"^##\s+(.+?)\s*$")
_H3_RE = re.compile(r"^###\s+(.+?)\s*$")
_SLUG_RE = re.compile(r"[^A-Za-z0-9_-]+")
_BOLD_LABEL_RE = re.compile(r"^\s*\*\*([A-Za-z][A-Za-z ]{1,30})\*\*\s*:")
_TESTBED_RE = re.compile(r"-\s+\*\*([^*]+)\*\*\s*:\s*(.+)$")
_COMPONENT_RE = re.compile(r"-\s+\*\*([^*]+?)\*\*")
# Matches the "name" portion of a component label — everything up to the
# first `(` (parenthetical expansion) or ` - ` (dash-separated description).
_COMPONENT_NAME_RE = re.compile(r"^([^(\-]+?)(?:\s*\(|\s+-\s+|$)")

_OVERVIEW_H2 = "overview"
_DEPLOYMENT_H2 = "deployment information"
_COMPONENTS_H2 = "components"
_TEST_COVERAGE_H2 = "test coverage"
_TECH_STACK_H2 = "technology stack"
_TECH_STACK_H2_ALT = "tech stack"


@dataclass
class TestCase:
    name: str
    body: str


@dataclass
class SuiteDoc:
    name: str
    overview: str = ""
    deployment: str = ""
    components: str = ""
    tech_stack: str = ""
    test_cases: list[TestCase] = field(default_factory=list)
    unknown_h2: list[tuple[str, str]] = field(default_factory=list)


def parse_markdown(text: str) -> SuiteDoc:
    """Parse a test-suite markdown file into a `SuiteDoc`.

    Fence-aware: headings inside fenced code blocks are ignored.
    Tolerates missing H2 sections — the returned `SuiteDoc` has empty strings
    for anything not present. An orphan H3 (no preceding H2) is attached to a
    synthetic empty H2 so content is never dropped.
    """
    name = ""
    h2_stack: list[tuple[str, list[str], list[tuple[str, list[str]]]]] = []
    current_h3: tuple[str, list[str]] | None = None
    in_fence = False

    for raw in text.splitlines():
        if _FENCE_RE.match(raw):
            in_fence = not in_fence
            _append_line(h2_stack, current_h3, raw)
            continue
        if in_fence:
            _append_line(h2_stack, current_h3, raw)
            continue
        if (m := _H1_RE.match(raw)) and not name:
            name = m.group(1).strip()
            continue
        if m := _H2_RE.match(raw):
            h2_stack.append((m.group(1).strip(), [], []))
            current_h3 = None
            continue
        if m := _H3_RE.match(raw):
            if not h2_stack:
                h2_stack.append(("", [], []))
            current_h3 = (m.group(1).strip(), [])
            h2_stack[-1][2].append(current_h3)
            continue
        _append_line(h2_stack, current_h3, raw)

    suite = SuiteDoc(name=name)
    for h2_title, h2_body, h3s in h2_stack:
        key = h2_title.strip().lower()
        body = "\n".join(h2_body).strip()
        if key == _OVERVIEW_H2:
            suite.overview = body
        elif key == _DEPLOYMENT_H2:
            suite.deployment = body
        elif key == _COMPONENTS_H2:
            suite.components = body
        elif key in (_TECH_STACK_H2, _TECH_STACK_H2_ALT):
            suite.tech_stack = body
        elif key == _TEST_COVERAGE_H2:
            for h3_title, h3_body in h3s:
                if not h3_title:
                    continue
                suite.test_cases.append(TestCase(name=h3_title, body="\n".join(h3_body).strip()))
        elif body or h3s:
            suite.unknown_h2.append((h2_title, body))
    return suite


def _append_line(
    h2_stack: list[tuple[str, list[str], list[tuple[str, list[str]]]]],
    h3: tuple[str, list[str]] | None,
    line: str,
) -> None:
    if h3 is not None:
        h3[1].append(line)
    elif h2_stack:
        h2_stack[-1][1].append(line)


def extract_testbed(deployment_body: str) -> str:
    """Pull the `**Testbed Type**: ...` value from Deployment Information.

    Prefers the short backtick-wrapped id (e.g. `vrops-1slice-config-ph`) over
    the long descriptor — it keeps the breadcrumb compact. Falls back to the
    full value if no backtick id is present.
    """
    for raw in deployment_body.splitlines():
        m = _TESTBED_RE.match(raw.strip())
        if m and "testbed" in m.group(1).strip().lower():
            value = m.group(2).strip()
            short = re.search(r"`([^`]+)`", value)
            return short.group(1) if short else value
    return ""


def extract_components(components_body: str) -> list[str]:
    """Pull short component names from each bullet in Components.

    The Access-Management template uses long bolded labels like
    `**Ops (vRealize Operations) - Platform core service for monitoring...**`.
    We want just `Ops` — the name up to the first `(` or ` - `.
    """
    out: list[str] = []
    for raw in components_body.splitlines():
        m = _COMPONENT_RE.match(raw.strip())
        if not m:
            continue
        full = m.group(1).strip()
        name_match = _COMPONENT_NAME_RE.match(full)
        label = name_match.group(1).strip() if name_match else full
        if label:
            out.append(label)
    return out


def build_breadcrumb(suite_name: str, testbed: str, components: list[str]) -> str:
    """Return the compact ≤ 2-line breadcrumb prepended to every chunk body."""
    line1 = f"# Suite: {suite_name}"
    parts: list[str] = []
    if testbed:
        parts.append(f"Testbed: {testbed}")
    if components:
        parts.append(f"Components: {', '.join(components)}")
    if not parts:
        return line1 + "\n"
    return f"{line1}\n# {' | '.join(parts)}\n"


def build_overview_body(suite: SuiteDoc) -> str:
    """Assemble the single per-suite overview chunk body."""
    parts: list[str] = []
    if suite.overview:
        parts.append(f"## Overview\n\n{suite.overview}")
    if suite.deployment:
        parts.append(f"## Deployment Information\n\n{suite.deployment}")
    if suite.components:
        parts.append(f"## Components\n\n{suite.components}")
    if suite.tech_stack:
        parts.append(f"## Technology Stack\n\n{suite.tech_stack}")
    return "\n\n".join(parts)


def slug(name: str) -> str:
    """Filename-safe slug. Preserves letters, digits, underscores, hyphens."""
    s = _SLUG_RE.sub("-", name).strip("-")
    return s or "unnamed"


def fit_to_budget(
    body: str,
    budget_tokens: int,
    token_count: Callable[[str], int],
) -> list[str]:
    """Split `body` into parts each ≤ `budget_tokens`.

    Strategy (ladder of increasing aggressiveness):
      1. Whole body fits → `[body]`.
      2. Split at `**Label**:` sub-section boundaries, greedy-pack into groups.
      3. If a single sub-section is still too large, split its lines and pack.
      4. If an individual line exceeds budget, yield it alone with a warning
         (caller may choose to emit regardless; we never drop content).
    """
    if token_count(body) <= budget_tokens:
        return [body]

    subs = _split_by_bold_labels(body)
    if len(subs) > 1:
        packed = _greedy_pack(subs, budget_tokens, token_count, joiner="\n\n")
        if packed is not None:
            return packed

    lines = [ln for ln in body.splitlines() if ln.strip()]
    packed = _greedy_pack(lines, budget_tokens, token_count, joiner="\n")
    if packed is not None:
        return packed

    return [body]


def _split_by_bold_labels(body: str) -> list[str]:
    """Group lines into chunks starting at each `**Label**:` boundary."""
    groups: list[list[str]] = [[]]
    for ln in body.splitlines():
        if _BOLD_LABEL_RE.match(ln) and any(s.strip() for s in groups[-1]):
            groups.append([ln])
        else:
            groups[-1].append(ln)
    return ["\n".join(g).strip() for g in groups if any(s.strip() for s in g)]


def _greedy_pack(
    fragments: list[str],
    budget: int,
    token_count: Callable[[str], int],
    joiner: str,
) -> list[str] | None:
    """Greedy-pack fragments into groups each ≤ budget. None if any single fragment exceeds budget."""
    out: list[str] = []
    current: list[str] = []
    for frag in fragments:
        if token_count(frag) > budget:
            return None
        candidate = joiner.join([*current, frag]) if current else frag
        if token_count(candidate) <= budget:
            current.append(frag)
        else:
            out.append(joiner.join(current))
            current = [frag]
    if current:
        out.append(joiner.join(current))
    return out


def render_chunk(breadcrumb: str, body: str) -> str:
    """Assemble the final chunk body = breadcrumb + blank line + body + trailing newline."""
    return f"{breadcrumb}\n{body.strip()}\n"


@dataclass(frozen=True)
class EmitConfig:
    """Per-splitter emit config. Concrete splitters build this from their options."""

    max_chunk_tokens: int
    emit_overview_chunk: bool
    context_fn: Callable[[str], str] | None  # (chunk_body) → context sentence; None to skip


def emit_chunks(
    path: Path,
    cfg: EmitConfig,
    token_count: Callable[[str], int],
) -> Iterator[SplitDoc]:
    """Parse `path` and yield one SplitDoc per overview + test case.

    Every chunk body is `breadcrumb + body`. Bodies are sub-split by bold-label
    boundaries if they exceed `cfg.max_chunk_tokens - breadcrumb_tokens`.
    """
    text = path.read_text(encoding="utf-8")
    suite = parse_markdown(text)
    suite_name = suite.name or path.stem
    suite_slug = slug(suite_name)
    testbed = extract_testbed(suite.deployment)
    components = extract_components(suite.components)
    breadcrumb = build_breadcrumb(suite_name, testbed, components)
    breadcrumb_tokens = token_count(breadcrumb)
    body_budget = max(50, cfg.max_chunk_tokens - breadcrumb_tokens)

    order = 0
    if cfg.emit_overview_chunk:
        overview_body = build_overview_body(suite)
        if overview_body:
            parts = fit_to_budget(overview_body, body_budget, token_count)
            single = len(parts) == 1
            for part_i, part in enumerate(parts):
                yield _build_doc(
                    suite_slug=suite_slug,
                    order=order,
                    section_slug="overview",
                    part_i=part_i,
                    single=single,
                    breadcrumb=breadcrumb,
                    body=part,
                    suite_name=suite_name,
                    case_name=None,
                    context_fn=cfg.context_fn,
                )
            order += 1

    for case in suite.test_cases:
        heading = f"### {case.name}"
        full_body = f"{heading}\n\n{case.body}".strip()
        parts = fit_to_budget(full_body, body_budget, token_count)
        single = len(parts) == 1
        for part_i, part in enumerate(parts):
            yield _build_doc(
                suite_slug=suite_slug,
                order=order,
                section_slug=slug(case.name),
                part_i=part_i,
                single=single,
                breadcrumb=breadcrumb,
                body=part,
                suite_name=suite_name,
                case_name=case.name,
                context_fn=cfg.context_fn,
            )
        order += 1


def _build_doc(
    *,
    suite_slug: str,
    order: int,
    section_slug: str,
    part_i: int,
    single: bool,
    breadcrumb: str,
    body: str,
    suite_name: str,
    case_name: str | None,
    context_fn: Callable[[str], str] | None,
) -> SplitDoc:
    if context_fn is not None:
        sentence = context_fn(body)
        if sentence:
            body = f"> _Context_: {sentence.strip()}\n\n{body}"
    rendered = render_chunk(breadcrumb, body)
    if single:
        filename = f"{suite_slug}__{order:02d}__{section_slug}.md"
    else:
        filename = f"{suite_slug}__{order:02d}__{section_slug}__p{part_i + 1}.md"
    return SplitDoc(
        origin_name=filename,
        body=rendered.encode("utf-8"),
        media_type="text/markdown",
        metadata={
            "suite_name": suite_name,
            "case_name": case_name,
            "kind": "overview" if case_name is None else "test_case",
            "order": order,
            "part": part_i,
        },
    )
