"""`test_suite_bge` — test-suite splitter tuned for BAAI/bge-small-en-v1.5 (512-token cap).

Emits one overview chunk + one chunk per test case, each ≤ 400 tokens so the
PAIS index (configured with chunk_size=512, chunk_overlap=64) keeps each chunk
atomic and doesn't slice a case mid-body.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from pydantic import Field

from pais.ingest.registry import register_splitter
from pais.ingest.splitters import _test_suite_core as core
from pais.ingest.splitters._base import SplitDoc, SplitterMeta, SplitterOptionsBase

_DEFAULT_BUDGET = 400  # tokens; leaves 112-token headroom under bge-small's 512 cap


class TestSuiteBgeOptions(SplitterOptionsBase):
    """Options for the `test_suite_bge` splitter (declared in the TOML `[splitter]` block)."""

    max_case_tokens: int = Field(
        default=_DEFAULT_BUDGET,
        gt=0,
        le=512,
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
class TestSuiteBgeSplitter:
    """Per-test-case atomic chunks with compact breadcrumb, tuned for BAAI/bge-small-en-v1.5."""

    kind: ClassVar[str] = "test_suite_bge"
    options_model: ClassVar[type[TestSuiteBgeOptions]] = TestSuiteBgeOptions
    meta: ClassVar[SplitterMeta] = SplitterMeta(
        summary="Per-test-case chunks with breadcrumb; tuned for bge-small-en-v1.5",
        input_type="structured test-suite markdown (# Suite / ## Test Coverage / ### testXxx)",
        algorithm=(
            "Parses H1 suite name + H2 context sections (Overview/Deployment/Components/Tech Stack) "
            "+ H3 test cases under `## Test Coverage`. Emits one overview chunk plus one chunk per "
            "test case, each prepended with a 2-line breadcrumb (`# Suite: X | Testbed: Y | Components: A, B`). "
            "Chunks are sized ≤ max_case_tokens so the index's chunk_size=512 keeps each atomic."
        ),
        chunk_size_unit="tokens",
        typical_chunk_size="200-400 tokens (~1 KB English)",
        token_char_hint="≈ 4 chars/token (English, BAAI/bge-small-en-v1.5)",
        example_input="~/Downloads/Access-Management.md (a structured test-suite file)",
        notes=(
            "Breadcrumb (≤ 60 tokens) is IN the chunk body so the embedding captures suite-level context "
            "even when the chunk is retrieved alone — this is what prevents the 'naked fragment' RAG failure.",
            "`group_key` is `<suite-slug>__` so `pais ingest --replace` cleanly replaces one suite at a time.",
            "`--with-context-llm` adds Anthropic-style contextual-retrieval on top (49% recall gain per Anthropic 2024, ~$1-3 for 300 suites with Haiku + prompt caching).",
        ),
        target_embeddings_model="BAAI/bge-small-en-v1.5",
        suggested_index_chunk_size=512,
        suggested_index_chunk_overlap=64,
    )

    def __init__(self, options: TestSuiteBgeOptions) -> None:
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
