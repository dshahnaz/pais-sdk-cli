"""Token-count wrapper around the bge-small-en-v1.5 tokenizer.

Uses the exact tokenizer the PAIS index uses so our budget enforcement
matches the server's chunking. Falls back to a char-based estimator when
the optional `tokenizers` dependency is missing OR when HuggingFace is
unreachable (e.g. corp networks blocking huggingface.co)."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

from pais.logging import get_logger

if TYPE_CHECKING:
    from tokenizers import Tokenizer  # type: ignore[import-untyped]

_log = get_logger("pais.tokenizer")

# Hard per-file cap for the splitter. Index chunk_size is 512 tokens; we
# keep 112-token (22%) headroom so tokenizer variance never pushes an
# emitted file over the limit and triggers server-side re-splitting.
BUDGET = 400

# Rough tokens reserved for the 3-line breadcrumb header.
HEADER_RESERVE = 20

_MODEL_ID = "BAAI/bge-small-en-v1.5"
_INSTALL_HINT = (
    "Install the dev extras to enable token counting:\n"
    "    pip install 'pais-sdk-cli[dev]'\n"
    "    # or:  uv sync --all-extras"
)


@lru_cache(maxsize=1)
def _tokenizer() -> Tokenizer | None:
    """Try to load the BGE tokenizer; return None on any failure (caller falls back to chars)."""
    try:
        from tokenizers import Tokenizer as _Tok
    except ImportError:
        _log.warning("pais.tokenizer.fallback_chars", reason="tokenizers package not installed")
        return None
    try:
        return _Tok.from_pretrained(_MODEL_ID)
    except Exception as e:
        # HF unreachable / SSL cert issue / 403 / rate limit / etc. on corp networks.
        _log.warning(
            "pais.tokenizer.fallback_chars",
            reason=f"failed to load {_MODEL_ID}",
            error=f"{type(e).__name__}: {e}",
        )
        return None


def token_count(text: str) -> int:
    """Return the number of tokens `text` encodes to under bge-small-en-v1.5,
    or a char-based estimate if the tokenizer is unavailable.
    """
    if not text:
        return 0
    tok = _tokenizer()
    if tok is None:
        # Char-based fallback. Approximate (~4 chars/token for English).
        return max(len(text) // 4, 1)
    return len(tok.encode(text).ids)
