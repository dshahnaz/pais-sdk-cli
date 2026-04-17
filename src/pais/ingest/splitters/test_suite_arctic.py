"""`test_suite_arctic` — test-suite splitter tuned for Snowflake/snowflake-arctic-embed-m-v2.0.

Arctic's 8192-token input window gives generous headroom. We still emit
per-test-case atomic chunks (retrieval precision peaks at 256-1024 tokens per
the 2026 benchmarks — going to 8 K is wasted capacity), but with a wider
budget (1500 tokens) that rarely sub-splits and lets the breadcrumb be richer
if needed. The matching index config is chunk_size=2048, chunk_overlap=256.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from pydantic import Field

from pais.ingest.registry import register_splitter
from pais.ingest.splitters import _test_suite_core as core
from pais.ingest.splitters._base import SplitDoc, SplitterMeta, SplitterOptionsBase

_DEFAULT_BUDGET = 1500  # tokens; generous headroom under arctic-m's 8192 cap


class TestSuiteArcticOptions(SplitterOptionsBase):
    """Options for the `test_suite_arctic` splitter (declared in the TOML `[splitter]` block)."""

    max_case_tokens: int = Field(
        default=_DEFAULT_BUDGET,
        gt=0,
        le=8192,
        description="Token budget per test-case chunk. Sub-splits a case if exceeded.",
    )
    emit_overview_chunk: bool = Field(
        default=True,
        description="Emit one per-suite chunk containing Overview + Deployment + Components + Tech Stack.",
    )
    with_context_llm: bool = Field(
        default=False,
        description="Prepend an Anthropic-generated context sentence to each chunk (requires [contextual] extra + ANTHROPIC_API_KEY).",
    )
    context_llm_model: str = Field(
        default="claude-haiku-4-5-20251001",
        description="Model id for --with-context-llm (default: Haiku 4.5).",
    )


@register_splitter
class TestSuiteArcticSplitter:
    """Per-test-case atomic chunks with breadcrumb, tuned for snowflake-arctic-embed-m-v2.0."""

    kind: ClassVar[str] = "test_suite_arctic"
    options_model: ClassVar[type[TestSuiteArcticOptions]] = TestSuiteArcticOptions
    meta: ClassVar[SplitterMeta] = SplitterMeta(
        summary="Per-test-case chunks with breadcrumb; tuned for arctic-embed-m-v2.0",
        input_type="structured test-suite markdown (# Suite / ## Test Coverage / ### testXxx)",
        algorithm=(
            "Same parsing + breadcrumb strategy as test_suite_bge, but with a wider 1500-token budget "
            "that leverages Arctic's 8192-token input window. Target index config chunk_size=2048 keeps "
            "each chunk atomic even when the case body is unusually long. Retrieval precision still "
            "peaks at 256-1024 tokens, so we don't push chunk sizes up to 8 K — Arctic's headroom is a "
            "safety margin, not a target."
        ),
        chunk_size_unit="tokens",
        typical_chunk_size="300-1200 tokens (most cases fit whole without sub-split)",
        token_char_hint="≈ 4 chars/token (English)",
        example_input="~/Downloads/Access-Management.md (a structured test-suite file)",
        notes=(
            "Budget counted with bge-small tokenizer (conservative — Arctic tokenizes ~same or slightly less).",
            "`group_key` is `<suite-slug>__` so `pais ingest --replace` cleanly replaces one suite at a time.",
            "`--with-context-llm` adds Anthropic-style contextual-retrieval on top (49% recall gain per Anthropic 2024).",
        ),
        target_embeddings_model="Snowflake/snowflake-arctic-embed-m-v2.0",
        suggested_index_chunk_size=2048,
        suggested_index_chunk_overlap=256,
    )

    def __init__(self, options: TestSuiteArcticOptions) -> None:
        self._options = options

    def group_key(self, path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        suite = core.parse_markdown(text)
        return f"{core.slug(suite.name or path.stem)}__"

    def split(self, path: Path) -> Iterator[SplitDoc]:
        from pais.dev.token_budget import token_count

        ctx_fn = None
        if self._options.with_context_llm:
            from pais.ingest.contextual import make_context_fn

            ctx_fn = make_context_fn(
                whole_doc=path.read_text(encoding="utf-8"),
                model=self._options.context_llm_model,
            )
        cfg = core.EmitConfig(
            max_chunk_tokens=self._options.max_case_tokens,
            emit_overview_chunk=self._options.emit_overview_chunk,
            context_fn=ctx_fn,
        )
        yield from core.emit_chunks(path, cfg, token_count)
