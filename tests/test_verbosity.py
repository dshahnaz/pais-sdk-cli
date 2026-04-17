"""Verbosity tiers: default CLI → WARNING, -v → INFO, -vv → DEBUG.

Also verifies that a successful HTTP request does NOT fire at INFO anymore —
it moved to DEBUG in v0.6.6. A prune-style sequence of 50 requests used to
dump 50 `pais.request` lines on stderr at INFO; now `-v` only surfaces
high-signal events (warnings, retries), and only `-vv` brings per-request
traces back."""

from __future__ import annotations

import logging

from typer.testing import CliRunner

from pais.cli.app import app
from pais.transport.fake_transport import FakeTransport
from pais_mock.state import Store


def _reset_root() -> None:
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.WARNING)


def test_cli_default_log_level_is_warning() -> None:
    _reset_root()
    runner = CliRunner()
    runner.invoke(app, ["--no-interactive"])
    assert logging.getLogger().level == logging.WARNING


def test_cli_v_flag_sets_info() -> None:
    _reset_root()
    runner = CliRunner()
    runner.invoke(app, ["-v", "--no-interactive"])
    assert logging.getLogger().level == logging.INFO


def test_cli_vv_flag_sets_debug() -> None:
    _reset_root()
    runner = CliRunner()
    runner.invoke(app, ["-vv", "--no-interactive"])
    assert logging.getLogger().level == logging.DEBUG


def test_transport_request_success_is_debug_not_info(caplog) -> None:  # type: ignore[no-untyped-def]
    """A successful round-trip must not emit `pais.request` at INFO or higher.
    This is the core of the 'silent by default' promise: users running prune
    or ingest at default verbosity should not see per-request log spam."""
    from pais.client import PaisClient
    from pais.models import KnowledgeBaseCreate

    store = Store()
    c = PaisClient(FakeTransport(store))
    caplog.set_level(logging.INFO)  # capture INFO and above
    caplog.clear()

    # A successful CRUD round-trip.
    c.knowledge_bases.create(KnowledgeBaseCreate(name="kb"))
    c.knowledge_bases.list()

    # structlog routes through stdlib logging. The `pais.request` event must
    # not appear at INFO.
    rendered = "\n".join(r.getMessage() for r in caplog.records)
    assert "pais.request" not in rendered, (
        "pais.request success lines should be DEBUG, not INFO — found:\n" + rendered
    )
