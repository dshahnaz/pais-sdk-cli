"""Tokenizer-backed helpers shared by the ingest splitters and preview.

Holds `pais.dev.token_budget.token_count` — the single place that loads the
`BAAI/bge-small-en-v1.5` tokenizer. All splitter-specific logic lives under
`pais.ingest.splitters.*`.
"""
