"""TTY safety + opt-outs for the bare-`pais` interactive trigger.

The menu must NEVER auto-open in non-TTY contexts (CI scripts, pipes), and
the user must always be able to disable it via `--no-interactive` or
`PAIS_NONINTERACTIVE=1`."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from pais.cli.app import app as cli_app


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setenv("PAIS_MODE", "mock")
    monkeypatch.setenv("PAIS_LOG_LEVEL", "WARNING")
    return CliRunner()


def test_bare_pais_in_non_tty_prints_help_not_menu(runner: CliRunner) -> None:
    """`runner.invoke` simulates a non-TTY stdin → must print help, exit 0."""
    r = runner.invoke(cli_app, [])
    assert r.exit_code == 0, r.output
    assert "Usage: " in r.output
    # The interactive shell would have printed this banner.
    assert "PAIS interactive shell" not in r.output


def test_no_interactive_flag_skips_menu(runner: CliRunner) -> None:
    r = runner.invoke(cli_app, ["--no-interactive"])
    assert r.exit_code == 0, r.output
    assert "Usage: " in r.output
    assert "PAIS interactive shell" not in r.output


def test_pais_noninteractive_env_var_skips_menu(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PAIS_NONINTERACTIVE", "1")
    r = runner.invoke(cli_app, [])
    assert r.exit_code == 0, r.output
    assert "Usage: " in r.output
    assert "PAIS interactive shell" not in r.output


def test_subcommand_runs_normally_under_runner(runner: CliRunner) -> None:
    """A real subcommand must work even though stdin isn't a TTY (this is the
    common scripting path)."""
    r = runner.invoke(cli_app, ["models", "list", "-o", "json"])
    assert r.exit_code == 0, r.output


def test_pais_shell_exits_in_non_tty(runner: CliRunner) -> None:
    """`pais shell` should refuse to start when stdin isn't a TTY."""
    r = runner.invoke(cli_app, ["shell"])
    assert r.exit_code == 1
    assert "TTY" in (r.output + (r.stderr or ""))
