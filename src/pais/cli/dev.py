"""Redirect shim for the removed `pais-dev` console script.

Anything `pais-dev` could do in v0.3 is now under `pais` directly:
    pais-dev ingest-suites <dir> --kb <kb> --index <ix>
        →  pais ingest <kb>:<ix> <dir>          (with the index's splitter set in pais.toml)
        →  pais ingest <kb>:<ix> <dir> --splitter test_suite_md   (one-shot override)
    pais-dev split-suite <file> --out <dir>
        →  pais ingest <kb>:<ix> <file> --dry-run        (uploads nothing; review the chunks)
    pais-dev ingest-suite <file> --kb <kb> --index <ix>
        →  pais ingest <kb>:<ix> <file>

This module exits 1 on every invocation so callers fail loudly instead of
silently no-opping. Removed entirely in v0.5.
"""

from __future__ import annotations

import sys

_MESSAGE = """\
pais-dev was removed in v0.4. Use `pais ingest` instead.

Quick translations:
  pais-dev ingest-suites <dir> --kb K --index I
      →  pais ingest K:I <dir>
  pais-dev ingest-suite <file> --kb K --index I
      →  pais ingest K:I <file>
  pais-dev split-suite <file> --out <dir>
      →  pais ingest K:I <file> --dry-run

`pais ingest` picks the splitter from your config's [splitter] block.
For one-off use, pass --splitter test_suite_md.

See: https://github.com/dshahnaz/pais-sdk-cli/blob/main/docs/migration-0.3-to-0.4.md
"""


def main() -> None:
    sys.stderr.write(_MESSAGE)
    sys.exit(1)


if __name__ == "__main__":
    main()
