"""Built-in splitters. Importing this module registers them all."""

from pais.ingest.splitters import markdown_headings, passthrough, test_suite_md, text_chunks

__all__ = ["markdown_headings", "passthrough", "test_suite_md", "text_chunks"]
