"""Deterministic simulations: chunking, embeddings, cosine search, chat echo."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Iterable

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    text = text.strip()
    if not text:
        return []
    parts = [p.strip() for p in _SENTENCE_RE.split(text) if p.strip()]
    return parts or [text]


def chunk_text(text: str, *, chunk_size: int = 400, chunk_overlap: int = 100) -> list[str]:
    """Sentence-aware chunking with approximate target size + overlap.

    Kept intentionally simple: not a production splitter, but deterministic
    enough to drive RAG flow tests.
    """
    sentences = split_sentences(text)
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for s in sentences:
        if size + len(s) > chunk_size and buf:
            chunks.append(" ".join(buf).strip())
            # overlap: keep tail sentences that cover `chunk_overlap` chars
            overlap_buf: list[str] = []
            overlap_size = 0
            for tail in reversed(buf):
                if overlap_size + len(tail) > chunk_overlap:
                    break
                overlap_buf.insert(0, tail)
                overlap_size += len(tail) + 1
            buf = overlap_buf
            size = overlap_size
        buf.append(s)
        size += len(s) + 1
    if buf:
        chunks.append(" ".join(buf).strip())
    return [c for c in chunks if c]


_EMBED_DIM = 64
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def fake_embed(text: str) -> list[float]:
    """Deterministic bag-of-tokens embedding.

    Each token hashes to one of `_EMBED_DIM` buckets and contributes +1.
    Normalized to unit length. Texts sharing tokens produce positive cosine
    similarity — enough signal to drive RAG flow tests, no ML dependency.
    """
    vec = [0.0] * _EMBED_DIM
    tokens = _tokenize(text) or [text]
    for tok in tokens:
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        bucket = int.from_bytes(h[:4], "big") % _EMBED_DIM
        # small 1/4-chance sign flip per token to break symmetry slightly
        sign = 1.0 if (h[4] & 1) == 0 else 0.9
        vec[bucket] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: Iterable[float], b: Iterable[float]) -> float:
    a = list(a)
    b = list(b)
    if not a or not b:
        return 0.0
    num = sum(x * y for x, y in zip(a, b, strict=False))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)
