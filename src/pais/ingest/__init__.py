"""Ingestion pipeline: per-index splitter registry + runner.

Public surface for embedding apps:
    from pais.ingest import SPLITTER_REGISTRY, get_splitter, SplitDoc, Splitter
    from pais.ingest.runner import ingest_path, IngestReport

CLI users never touch this directly — `pais ingest` does.
"""

from pais.ingest.registry import SPLITTER_REGISTRY, get_splitter, register_splitter
from pais.ingest.splitters._base import SplitDoc, Splitter

__all__ = [
    "SPLITTER_REGISTRY",
    "SplitDoc",
    "Splitter",
    "get_splitter",
    "register_splitter",
]
