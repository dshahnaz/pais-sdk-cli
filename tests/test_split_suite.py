"""Splitter tests: synthetic fixture + optional real-file golden test."""

from __future__ import annotations

from pathlib import Path

import pytest

from pais.dev.split_suite import SectionTooLargeError, split_suite, write_sections
from pais.dev.token_budget import BUDGET, token_count

_SYNTH = """# Sample-Suite

## Overview

This is the overview.

## Deployment Information

- Testbed: sample
- Driver: Linux

## Components

- svc-a
- svc-b

## Test Coverage

### testAlpha

Purpose: validates alpha path.

### testBeta

Purpose: validates beta path. Depends on testAlpha.

### test with spaces & punct!

Weird name on purpose.

## Technology Stack

- Python
- pytest
"""


def _write(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text)
    return p


def test_splits_synthetic_into_expected_sections(tmp_path: Path) -> None:
    p = _write(tmp_path, "Sample-Suite.md", _SYNTH)
    sections = split_suite(p)
    kinds = [s.kind for s in sections]
    names = [s.section_name for s in sections]
    assert kinds[0] == "overview"
    assert kinds[-1] == "tech_stack"
    assert "testAlpha" in names
    assert "testBeta" in names


def test_every_emitted_section_fits_budget(tmp_path: Path) -> None:
    p = _write(tmp_path, "Sample-Suite.md", _SYNTH)
    for s in split_suite(p):
        n = token_count(s.rendered)
        assert n <= BUDGET, f"{s.filename} = {n} tokens > {BUDGET}"


def test_breadcrumb_header_in_every_rendered_file(tmp_path: Path) -> None:
    p = _write(tmp_path, "Sample-Suite.md", _SYNTH)
    for s in split_suite(p):
        assert s.rendered.startswith(f"# Suite: Sample-Suite\n## Section: {s.section_name}\n")
        assert f"## Kind: {s.kind}" in s.rendered


def test_slug_sanitization_in_filename(tmp_path: Path) -> None:
    p = _write(tmp_path, "Sample-Suite.md", _SYNTH)
    sections = split_suite(p)
    weird = next(s for s in sections if "punct" in s.section_name)
    # Filename contains slug-safe chars only; '&', '!', spaces replaced with '_'.
    assert "test_with_spaces" in weird.filename
    assert " " not in weird.filename
    assert "&" not in weird.filename
    assert "!" not in weird.filename


def test_oversized_section_is_sub_split(tmp_path: Path) -> None:
    # Build a very long section by repeating paragraphs.
    big_para = ("This paragraph describes validations and key operations in detail. " * 12).strip()
    body = "\n\n".join([big_para] * 12)  # many paragraphs, total ~4k tokens
    md = f"""# Big-Suite

## Test Coverage

### testHuge

{body}
"""
    p = _write(tmp_path, "Big-Suite.md", md)
    sections = split_suite(p)
    test_parts = [s for s in sections if s.section_name == "testHuge"]
    assert len(test_parts) >= 2
    for s in test_parts:
        assert token_count(s.rendered) <= BUDGET
        assert s.part is not None
        assert s.part >= 1
        assert "part" in s.filename


def test_single_indivisible_monster_sentence_raises(tmp_path: Path) -> None:
    # One paragraph, one sentence, >>400 tokens and no split points.
    monster = ("word " * 600).strip() + "."
    md = f"""# Bad-Suite

## Test Coverage

### testMonster

{monster}
"""
    p = _write(tmp_path, "Bad-Suite.md", md)
    with pytest.raises(SectionTooLargeError):
        split_suite(p)


def test_write_sections_puts_files_on_disk(tmp_path: Path) -> None:
    p = _write(tmp_path, "Sample-Suite.md", _SYNTH)
    sections = split_suite(p)
    out = tmp_path / "out"
    paths = write_sections(sections, out)
    assert len(paths) == len(sections)
    for path in paths:
        assert path.exists()
        assert path.read_text().startswith("# Suite: Sample-Suite\n")


@pytest.mark.skipif(
    not (Path.home() / "Downloads" / "Access-Management.md").exists(),
    reason="real fixture ~/Downloads/Access-Management.md not present",
)
def test_real_access_management_fixture() -> None:
    path = Path.home() / "Downloads" / "Access-Management.md"
    sections = split_suite(path)
    assert len(sections) >= 12, f"expected at least 12 sections, got {len(sections)}"
    assert all(s.filename.startswith("Access-Management__") for s in sections)
    counts = [token_count(s.rendered) for s in sections]
    assert max(counts) <= BUDGET
    # Emit distribution stats for human visibility (captured by pytest).
    counts.sort()
    p50 = counts[len(counts) // 2]
    p95 = counts[int(0.95 * (len(counts) - 1))]
    print(
        f"\nreal-fixture token distribution: min={counts[0]} p50={p50} p95={p95} "
        f"max={counts[-1]} budget={BUDGET}"
    )
