"""Short-flag aliases: every option in `_flags.py` exposes a -x form, and
`-h` is wired globally on the root Typer app so every subcommand inherits it."""

from __future__ import annotations

import pytest
import typer
from typer.testing import CliRunner

from pais.cli import _flags
from pais.cli.app import app as cli_app


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setenv("PAIS_MODE", "mock")
    monkeypatch.setenv("PAIS_LOG_LEVEL", "WARNING")
    return CliRunner()


# Every shared option must be a typer.OptionInfo carrying both --long and -short.
SHARED_OPTS = [
    ("OUTPUT_OPT", "--output", "-o"),
    ("PROFILE_OPT", "--profile", "-p"),
    ("YES_OPT", "--yes", "-y"),
    ("DRY_RUN_OPT", "--dry-run", "-n"),
    ("VERBOSE_OPT", "--verbose", "-v"),
    ("WORKERS_OPT", "--workers", "-w"),
    ("REPLACE_OPT", "--replace", "-r"),
    ("REPORT_OPT", "--report", "-R"),
    ("SPLITTER_OPT", "--splitter", "-s"),
    ("WITH_COUNTS_OPT", "--with-counts", "-c"),
    ("EPOCH_OPT", "--epoch", "-e"),
    ("FORCE_OPT", "--force", "-f"),
]


@pytest.mark.parametrize("name,long_form,short_form", SHARED_OPTS)
def test_shared_option_has_long_and_short(name: str, long_form: str, short_form: str) -> None:
    opt = getattr(_flags, name)
    assert isinstance(opt, typer.models.OptionInfo), f"{name} is not a typer.Option"
    decls = opt.param_decls
    assert long_form in decls, f"{name}: missing long form {long_form}; have {decls}"
    assert short_form in decls, f"{name}: missing short form {short_form}; have {decls}"


def test_help_option_names_constant_pairs_h_and_long() -> None:
    assert _flags.HELP_OPTION_NAMES == {"help_option_names": ["-h", "--help"]}


def test_root_help_with_short_flag(runner: CliRunner) -> None:
    r = runner.invoke(cli_app, ["-h"])
    assert r.exit_code == 0, r.output
    assert "Usage: " in r.output
    # Both long and short forms appear in the help.
    assert "--help" in r.output
    assert "-h" in r.output


def test_subcommand_inherits_short_help(runner: CliRunner) -> None:
    """Every subcommand group must inherit -h from the root context_settings."""
    for cmd in (["kb", "-h"], ["index", "-h"], ["ingest", "-h"], ["status", "-h"]):
        r = runner.invoke(cli_app, cmd)
        assert r.exit_code == 0, f"{cmd}: {r.output}"
        assert "Usage: " in r.output


def test_status_short_flags_resolve(runner: CliRunner) -> None:
    """Every short flag on `pais status` is parseable end-to-end."""
    r = runner.invoke(cli_app, ["status", "--no-ping", "-c", "-e", "-o", "json"])
    assert r.exit_code == 0, r.output


def test_kb_list_short_flags_resolve(runner: CliRunner) -> None:
    r = runner.invoke(cli_app, ["kb", "list", "-c", "-e", "-o", "json"])
    assert r.exit_code == 0, r.output
