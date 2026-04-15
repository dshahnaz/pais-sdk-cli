"""Token-count wrapper around the bge-small-en-v1.5 tokenizer.

Uses the exact tokenizer the PAIS index uses so our budget enforcement
matches the server's chunking. Falls back to a helpful ImportError when
the optional `tokenizers` dependency is missing.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tokenizers import Tokenizer  # type: ignore[import-untyped]

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
def _tokenizer() -> Tokenizer:
    try:
        from tokenizers import Tokenizer as _Tok
    except ImportError as e:
        raise ImportError(
            f"The 'tokenizers' package is required for token counting.\n{_INSTALL_HINT}"
        ) from e
    return _Tok.from_pretrained(_MODEL_ID)


def token_count(text: str) -> int:
    """Return the number of tokens `text` encodes to under bge-small-en-v1.5."""
    if not text:
        return 0
    return len(_tokenizer().encode(text).ids)
