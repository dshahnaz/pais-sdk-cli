"""Tests for `pais.cli._introspect`: every typer command in the live `app`
is enumerated with the right path / params / help."""

from __future__ import annotations

from pais.cli._introspect import walk
from pais.cli.app import app


def test_walk_returns_known_top_level_commands() -> None:
    specs = walk(app)
    paths = {s.path for s in specs}
    # Sanity — every top-level command we shipped is present.
    assert ("status",) in paths
    assert ("shell",) in paths
    assert ("ingest",) in paths  # group with invoke_without_command callback
    # Pick a representative leaf from each major group.
    assert ("kb", "list") in paths
    assert ("kb", "show") in paths
    assert ("kb", "ensure") in paths
    assert ("index", "list") in paths
    assert ("index", "delete") in paths
    assert ("agent", "list") in paths
    assert ("config", "show") in paths
    assert ("splitters", "list") in paths
    assert ("alias", "list") in paths


def test_kb_show_params_introspect_correctly() -> None:
    spec = next(s for s in walk(app) if s.path == ("kb", "show"))
    by_name = {p.name: p for p in spec.params}
    assert by_name["kb_ref"].kind == "argument"
    assert by_name["kb_ref"].required is True
    assert by_name["epoch"].kind == "option"
    assert by_name["epoch"].required is False
    assert by_name["epoch"].default is False
    assert by_name["output"].default == "table"


def test_ingest_callback_picked_up_as_leaf() -> None:
    """`ingest` is a Typer group with `invoke_without_command=True` — its
    callback should appear as a leaf with the target/path arguments."""
    spec = next(s for s in walk(app) if s.path == ("ingest",))
    arg_names = [p.name for p in spec.params if p.kind == "argument"]
    assert arg_names == ["target", "path"]
    opt_names = {p.name for p in spec.params if p.kind == "option"}
    assert "splitter_kind" in opt_names
    assert "replace" in opt_names
    assert "workers" in opt_names


def test_help_first_line_extracted() -> None:
    """`help=` overrides the docstring; first non-blank line wins."""
    spec = next(s for s in walk(app) if s.path == ("kb", "show"))
    assert spec.help == "KB header + per-index breakdown."
