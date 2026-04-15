"""Developer-time helpers for ingesting structured markdown into PAIS KBs.

Split suites into per-section files, enforce a token budget against the
`BAAI/bge-small-en-v1.5` tokenizer, upload them via the SDK.
"""
