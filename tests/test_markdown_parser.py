"""Markdown heading parser tests."""

from __future__ import annotations

from pais.dev.markdown import parse, render_body


def test_extracts_h1_title() -> None:
    doc = parse("# My Suite\n\n## Overview\nText.")
    assert doc.title == "My Suite"


def test_groups_h2_sections_with_h3_children() -> None:
    md = """# S

## Overview

Intro.

## Test Coverage

### testA

Body A.

### testB

Body B.
"""
    doc = parse(md)
    titles = [s.title for s in doc.sections]
    assert titles == ["Overview", "Test Coverage"]
    test_section = doc.sections[1]
    assert [h3.title for h3 in test_section.children] == ["testA", "testB"]
    assert "Body A." in render_body(test_section.children[0])
    assert "Body B." in render_body(test_section.children[1])


def test_h3_without_h2_is_orphan_ok() -> None:
    doc = parse("# S\n\n### lone_test\nBody.\n")
    # Parser creates a synthetic empty H2 to host the orphan.
    assert doc.sections[0].children[0].title == "lone_test"


def test_heading_inside_fenced_code_block_is_ignored() -> None:
    md = """# S

## Intro

```
# Not a heading
## Also not a heading
```

Real paragraph.
"""
    doc = parse(md)
    assert len(doc.sections) == 1
    assert doc.sections[0].title == "Intro"
    body = render_body(doc.sections[0])
    assert "# Not a heading" in body


def test_trailing_whitespace_in_titles_is_stripped() -> None:
    doc = parse("#   Spaced  \n\n##   Hello   \nBody.")
    assert doc.title == "Spaced"
    assert doc.sections[0].title == "Hello"


def test_empty_document() -> None:
    doc = parse("")
    assert doc.title == ""
    assert doc.sections == []
