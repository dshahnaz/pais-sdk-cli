"""Hidden typer options must reach `ParamSpec.hidden=True` and be skipped by
the interactive shell's prompt loop (v0.7.1).

This is load-bearing for `agent create`: `--kb-search-tool` stays callable
from scripts but must NOT prompt the user in the shell, because its picker
(`pick_mcp_tool`) hits an undocumented endpoint that may 500 or shape-drift.
"""

from __future__ import annotations

import typer

from pais.cli._introspect import walk
from pais.cli.app import app as cli_app


def _find(path: tuple[str, ...]):  # type: ignore[no-untyped-def]
    for spec in walk(cli_app):
        if spec.path == path:
            return spec
    raise AssertionError(f"command {path} not found in app")


def test_agent_create_kb_search_tool_is_hidden() -> None:
    """The legacy MCP flag still exists but is hidden from the shell."""
    spec = _find(("agent", "create"))
    params = {p.name: p for p in spec.params}
    assert "kb_search_tool" in params, "flag must remain for scripted back-compat"
    assert params["kb_search_tool"].hidden is True
    # The new doc-aligned fields are visible.
    assert params["index_id"].hidden is False
    assert params["index_top_n"].hidden is False
    assert params["index_similarity_cutoff"].hidden is False


def test_hidden_default_false_for_visible_options() -> None:
    """Every other option across the tree should default to hidden=False."""
    hidden_count = 0
    for spec in walk(cli_app):
        for p in spec.params:
            if p.hidden:
                hidden_count += 1
    # We intentionally hide at least one (agent create --kb-search-tool).
    assert hidden_count >= 1


def test_introspect_reads_typer_hidden_attr() -> None:
    """Build a throwaway typer command with hidden=True; confirm `walk`
    propagates the flag — proves the `getattr(info, "hidden", False)` wire."""
    tiny = typer.Typer()

    @tiny.command("t")
    def _t(
        visible: str = typer.Option("v", "--visible"),
        secret: str = typer.Option("s", "--secret", hidden=True),
    ) -> None:
        pass

    specs = walk(tiny)
    params = {p.name: p for p in specs[0].params}
    assert params["visible"].hidden is False
    assert params["secret"].hidden is True
