"""Shared `typer.Option(...)` constants — every CLI command imports its options
from here so long+short forms stay uniform and a new short form is a one-line
edit instead of a sweep across every command file.

Convention table (mirrored in docs/v0.4-plan.md and the `--help` output):

| long           | short | meaning                                       |
|----------------|-------|-----------------------------------------------|
| --profile      | -p    | active profile                                |
| --output       | -o    | table | json | yaml                          |
| --yes          | -y    | skip confirmation prompt                      |
| --dry-run      | -n    | preview only (rsync/make convention)          |
| --verbose      | -v    | verbose logs                                  |
| --workers      | -w    | worker pool size                              |
| --replace      | -r    | replace existing docs                         |
| --report       | -R    | report file path (capital — `-r` taken)       |
| --splitter     | -s    | override splitter kind                        |
| --with-counts  | -c    | include doc counts                            |
| --epoch        | -e    | print epoch ints instead of human dates       |
| --force        | -f    | force a destructive op                        |

`--prune` and `--no-ping` intentionally have no short form: destructive (or
negation) flags should be typed in full so they're never confused with
single-letter combos.
"""

from __future__ import annotations

from pathlib import Path

import typer

OUTPUT_OPT = typer.Option("table", "--output", "-o", help="table | json | yaml")
PROFILE_OPT = typer.Option(None, "--profile", "-p", help="Profile name within the config file")
YES_OPT = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt")
DRY_RUN_OPT = typer.Option(False, "--dry-run", "-n", help="Preview only; no writes.")
VERBOSE_OPT = typer.Option(False, "--verbose", "-v", help="Verbose logs.")
WORKERS_OPT = typer.Option(4, "--workers", "-w", min=1, max=32, help="Worker pool size.")
REPLACE_OPT = typer.Option(
    False,
    "--replace",
    "-r",
    help=(
        "Before uploading, delete docs whose origin_name starts with the splitter's "
        "group_key for each input file. Other docs in the index are untouched."
    ),
)
REPORT_OPT = typer.Option(
    Path("./ingest-report.json"),
    "--report",
    "-R",
    help="Where to write the JSON ingest report.",
)
SPLITTER_OPT = typer.Option(
    None, "--splitter", "-s", help="Override the splitter declared in the config for this index."
)
WITH_COUNTS_OPT = typer.Option(
    False,
    "--with-counts",
    "-c",
    help="Include indexes_count + documents_count (one extra round-trip per KB).",
)
EPOCH_OPT = typer.Option(
    False, "--epoch", "-e", help="Print epoch timestamps instead of human dates."
)
FORCE_OPT = typer.Option(False, "--force", "-f", help="Force the destructive op.")
QUICK_CONFIRM_OPT = typer.Option(
    False,
    "--quick-confirm",
    "-Q",
    help="In the interactive shell, fall back to y/N for destructive ops (skip type-to-confirm).",
)

# Wire `-h` globally on every Typer app via this dict.
HELP_OPTION_NAMES = {"help_option_names": ["-h", "--help"]}
