"""Anthropic contextual-retrieval helper for the test-suite splitters.

When `--with-context-llm` is passed (or `with_context_llm=True` in options),
each chunk gets a one-sentence LLM-generated description of its role within
the whole document, prepended to the chunk body before embedding.

The whole-document prompt block uses **ephemeral prompt caching** so the N
chunks from one suite re-use the same cached input — at Haiku prices with
caching, 300 suites x ~15 cases costs ~$1-3 total.

Anthropic is an optional dependency (install extra: `pip install pais-sdk-cli[contextual]`).
Importing this module does NOT import Anthropic; `make_context_fn` raises a
clean error with install instructions if the extra is missing.
"""

from __future__ import annotations

import os
from collections.abc import Callable

_INSTALL_HINT = (
    "Contextual retrieval requires the Anthropic SDK:\n"
    "    pip install 'pais-sdk-cli[contextual]'\n"
    "You also need the ANTHROPIC_API_KEY env var."
)

_SYSTEM_TEMPLATE = (
    "You help prepare document chunks for retrieval-augmented generation (RAG). "
    "The user gives you one chunk; you give back a single sentence that situates "
    "the chunk inside the document below, so the chunk's embedding vector captures "
    "that context.\n\n"
    "<document>\n{doc}\n</document>"
)

_USER_TEMPLATE = (
    "<chunk>\n{chunk}\n</chunk>\n\n"
    "Return only a single sentence describing how this chunk fits into the document above. "
    "No preamble, no quotes, no headings. Under 35 words."
)


def make_context_fn(*, whole_doc: str, model: str) -> Callable[[str], str]:
    """Return a callable `(chunk_body) -> context_sentence`.

    Raises `ImportError` with an install hint if the Anthropic SDK is missing,
    or `RuntimeError` if `ANTHROPIC_API_KEY` is unset.
    """
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise ImportError(_INSTALL_HINT) from e

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Export it in your shell, then retry.\n"
            "(Get a key at https://console.anthropic.com/)"
        )

    client = Anthropic()
    system_text = _SYSTEM_TEMPLATE.format(doc=whole_doc)

    def fn(chunk_body: str) -> str:
        resp = client.messages.create(
            model=model,
            max_tokens=128,
            system=[
                {
                    "type": "text",
                    "text": system_text,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": _USER_TEMPLATE.format(chunk=chunk_body)}],
        )
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                return str(getattr(block, "text", "")).strip()
        return ""

    return fn
