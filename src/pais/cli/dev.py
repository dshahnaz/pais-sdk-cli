"""Redirect shim for the removed `pais-dev` console script.

The `pais-dev` console-script entry was dropped in v0.5.0. This module is
kept only so `python -m pais.cli.dev` from a stale install still prints a
useful redirect instead of an ImportError. Will be deleted in v0.6.
"""

from __future__ import annotations

import sys

_MESSAGE = """\
pais-dev was removed in v0.5.0. Use `pais ingest` instead.

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
