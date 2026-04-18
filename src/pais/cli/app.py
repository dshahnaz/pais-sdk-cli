"""`pais` CLI entrypoint."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

import typer

from pais.cli import _alias, _kb_show, config_cmd
from pais.cli._config_file import load_profile_config
from pais.cli._flags import (
    EPOCH_OPT,
    HELP_OPTION_NAMES,
    OUTPUT_OPT,
    WITH_COUNTS_OPT,
    YES_OPT,
)
from pais.cli._output import exit_code_for, render
from pais.cli.doctor_cmd import doctor as doctor_cmd
from pais.cli.ensure_cmd import kb_ensure
from pais.cli.ingest_cmd import alias_app, ingest_app, splitters_app
from pais.cli.logs_cmd import app as logs_app
from pais.cli.shell_cmd import shell as shell_cmd
from pais.cli.status_cmd import status as status_cmd
from pais.client import PaisClient
from pais.config import Settings, set_runtime_overrides
from pais.errors import PaisError
from pais.models import (
    AgentCreate,
    ChatCompletionRequest,
    ChatMessage,
    IndexCreate,
    KnowledgeBaseCreate,
    SearchQuery,
    ToolLink,
    ToolLinkType,
)

app = typer.Typer(
    help="PAIS SDK + CLI — talk to VMware Private AI Service or a local mock.",
    context_settings=HELP_OPTION_NAMES,
)
kb_app = typer.Typer(help="Knowledge Base commands", context_settings=HELP_OPTION_NAMES)
index_app = typer.Typer(
    help="Index commands (nested under a KB)", context_settings=HELP_OPTION_NAMES
)
agent_app = typer.Typer(help="Agent commands", context_settings=HELP_OPTION_NAMES)
mcp_app = typer.Typer(help="MCP tool discovery", context_settings=HELP_OPTION_NAMES)
models_app = typer.Typer(help="Model discovery", context_settings=HELP_OPTION_NAMES)
mock_app = typer.Typer(help="Run the local PAIS mock server", context_settings=HELP_OPTION_NAMES)
app.add_typer(kb_app, name="kb")
app.add_typer(index_app, name="index")
app.add_typer(agent_app, name="agent")
app.add_typer(mcp_app, name="mcp")
app.add_typer(models_app, name="models")
app.add_typer(mock_app, name="mock")
app.add_typer(config_cmd.app, name="config")
app.add_typer(ingest_app, name="ingest")
app.add_typer(splitters_app, name="splitters")
app.add_typer(alias_app, name="alias")
app.command("status")(status_cmd)
app.command("shell", help="Open the interactive PAIS menu (force).")(shell_cmd)
app.command("doctor", help="Run a diagnostic probe battery and emit a shareable report.")(
    doctor_cmd
)
app.add_typer(logs_app, name="logs")


def _print_version_and_exit(value: bool) -> None:
    if value:
        from pais import __version__

        typer.echo(f"pais {__version__}")
        raise typer.Exit()


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    config: Path | None = typer.Option(
        None, "--config", help="Path to a TOML config file (overrides discovery)"
    ),
    profile: str | None = typer.Option(
        None, "--profile", "-p", help="Profile name within the config file"
    ),
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        is_eager=True,
        callback=_print_version_and_exit,
        help="Show version and exit",
    ),
    no_interactive: bool = typer.Option(
        False,
        "--no-interactive",
        help="Disable bare-`pais` dropping into the interactive menu.",
    ),
    quick_confirm: bool = typer.Option(
        False,
        "--quick-confirm",
        "-Q",
        help="Use y/N for destructive ops in the shell (skip type-to-confirm).",
    ),
    verbose: int = typer.Option(
        0,
        "--verbose",
        "-v",
        count=True,
        help=(
            "Verbosity tier. Default (no flag) = WARNING; "
            "-v = INFO (purge decisions, ingest start/done); "
            "-vv = DEBUG (every HTTP request)."
        ),
    ),
) -> None:
    """Pin --config / --profile so every subcommand's Settings() picks them up.

    When invoked with no subcommand AND stdin is a TTY AND `--no-interactive`
    isn't set AND `PAIS_NONINTERACTIVE` env isn't set, drop into the
    interactive menu. Non-TTY callers (scripts, pipes) get the help banner.

    Verbosity contract (applies to both interactive and non-interactive):
      no flag → WARNING (only warnings/errors; e.g. TLS-verify-off, purge fallback)
      -v     → INFO (high-signal events)
      -vv    → DEBUG (per-request HTTP traces, full payloads)
    """
    set_runtime_overrides(config_path=config, profile=profile)
    if quick_confirm:
        os.environ["PAIS_QUICK_CONFIRM"] = "1"
    if verbose >= 1:
        os.environ["PAIS_VERBOSE"] = str(verbose)

    # Apply the verbosity tier eagerly — before any command runs — so even
    # the first HTTP request respects it. Without this, users would only see
    # log changes after the first `PaisClient.from_settings` call.
    from pais.logging import configure_logging as _configure_logging

    _cli_log_level = "WARNING" if verbose == 0 else ("INFO" if verbose == 1 else "DEBUG")
    _configure_logging(level=_cli_log_level)
    # Eager validation: surface config-file errors here with a clean message
    # rather than letting them bubble up as a Python traceback later.
    from pais.cli._config_file import ConfigError, load_profile

    try:
        load_profile(path=config, profile=profile)
    except ConfigError as e:
        typer.echo(f"config error: {e}", err=True)
        raise typer.Exit(code=1) from e

    if ctx.invoked_subcommand is not None:
        return
    if no_interactive or os.environ.get("PAIS_NONINTERACTIVE"):
        typer.echo(ctx.get_help())
        raise typer.Exit()
    if not sys.stdin.isatty():
        typer.echo(ctx.get_help())
        raise typer.Exit()
    from pais.cli.interactive import enter_interactive

    enter_interactive(app)


def _client() -> PaisClient:
    return Settings().build_client()


def _resolve_kb(client: PaisClient, kb_ref: str) -> str:
    """Resolve a KB ref (alias or UUID) to a UUID, using the active profile's config."""
    cfg, _, profile = load_profile_config()
    return _alias.resolve_kb(client, profile, kb_ref, cfg=cfg)


def _resolve_index(client: PaisClient, kb_ref: str, idx_ref: str) -> tuple[str, str]:
    cfg, _, profile = load_profile_config()
    return _alias.resolve_index(client, profile, kb_ref, idx_ref, cfg=cfg)


def _fmt_ts(value: object, *, epoch: bool) -> str:
    """Render an epoch int as either the raw int or a human UTC date."""
    import datetime as _dt

    if value in (None, "", 0):
        return "—"
    if epoch:
        return str(value)
    try:
        ts = int(value)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return str(value)
    if ts <= 0:
        return "—"
    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _confirm(message: str, *, yes: bool) -> None:
    if yes:
        return
    if not sys.stdin.isatty():
        typer.echo(f"refusing destructive op without --yes (non-interactive): {message}", err=True)
        raise typer.Exit(code=1)
    if not typer.confirm(message, default=False):
        typer.echo("aborted")
        raise typer.Exit(code=1)


def _run(fn: Callable[[], None]) -> None:
    try:
        fn()
    except PaisError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=exit_code_for(e)) from e
    except typer.BadParameter:
        raise
    except Exception as e:  # pragma: no cover
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=1) from e


# --- KB -----------------------------------------------------------------------
@kb_app.command("list")
def kb_list(
    with_counts: bool = WITH_COUNTS_OPT,
    epoch: bool = EPOCH_OPT,
    output: str = OUTPUT_OPT,
) -> None:
    def go() -> None:
        with _client() as c:
            kbs = c.knowledge_bases.list().data
            rows: list[dict[str, object]] = []
            for kb in kbs:
                row: dict[str, object] = {
                    "id": kb.id,
                    "name": kb.name,
                    "data_origin_type": getattr(kb.data_origin_type, "value", kb.data_origin_type),
                    "description": kb.description or "",
                    "created": _fmt_ts(kb.created_at, epoch=epoch),
                    "updated": _fmt_ts(getattr(kb, "last_updated_at", None), epoch=epoch),
                }
                if with_counts:
                    try:
                        indexes = c.indexes.list(kb.id).data
                    except PaisError as e:
                        # One bad KB shouldn't sink the whole command. Mark the
                        # row and surface the server's validation detail on
                        # stderr so the user can see *which* field the server
                        # rejected for this KB.
                        row["indexes"] = "!"
                        row["documents"] = "!"
                        typer.echo(
                            f"warn: kb={kb.name} indexes unavailable: {e}",
                            err=True,
                        )
                    else:
                        row["indexes"] = len(indexes)
                        row["documents"] = sum(getattr(i, "num_documents", 0) or 0 for i in indexes)
                rows.append(row)
            cols = ["id", "name", "data_origin_type", "created", "updated"]
            if with_counts:
                cols += ["indexes", "documents"]
            render(rows, fmt=output, columns=cols)

    _run(go)


@kb_app.command("create")
def kb_create(
    name: str = typer.Option(...),
    description: str | None = typer.Option(None),
    output: str = OUTPUT_OPT,
) -> None:
    def go() -> None:
        with _client() as c:
            kb = c.knowledge_bases.create(KnowledgeBaseCreate(name=name, description=description))
            render(kb, fmt=output)

    _run(go)


kb_app.command("ensure")(kb_ensure)


@kb_app.command("show")
def kb_show(
    kb_ref: str = typer.Argument(..., help="KB alias (from your config) or UUID."),
    epoch: bool = EPOCH_OPT,
    output: str = OUTPUT_OPT,
) -> None:
    """KB header + per-index breakdown."""

    def go() -> None:
        cfg, _, profile = load_profile_config()
        with _client() as c:
            kb_uuid = _alias.resolve_kb(c, profile, kb_ref, cfg=cfg)
            kb, indexes = _kb_show.fetch(c, kb_uuid)
            if output == "table":
                _kb_show.render_table(kb, indexes, epoch=epoch)
            else:
                from pais.cli._output import render

                render(_kb_show.to_dict(kb, indexes, epoch=epoch), fmt=output)

    _run(go)


@kb_app.command("get")
def kb_get(kb_ref: str, output: str = OUTPUT_OPT) -> None:
    def go() -> None:
        with _client() as c:
            kb_uuid = _resolve_kb(c, kb_ref)
            render(c.knowledge_bases.get(kb_uuid), fmt=output)

    _run(go)


@kb_app.command("delete")
def kb_delete(kb_ref: str, yes: bool = YES_OPT) -> None:
    """Delete a KB (cascades indexes + documents)."""
    _confirm(f"delete KB {kb_ref} and all its indexes/documents?", yes=yes)

    def go() -> None:
        with _client() as c:
            kb_uuid = _resolve_kb(c, kb_ref)
            c.knowledge_bases.delete(kb_uuid)
            typer.echo(f"deleted {kb_ref}")

    _run(go)


@kb_app.command("purge")
def kb_purge(
    kb_id: str,
    yes: bool = YES_OPT,
    strategy: str = typer.Option("auto", "--strategy", help="auto | api | recreate"),
    output: str = OUTPUT_OPT,
) -> None:
    """Delete every document in every index under the KB. KB itself stays."""
    _confirm(f"purge all documents under KB {kb_id}?", yes=yes)

    def go() -> None:
        from pais.cli._purge_progress import purge_progress

        with _client() as c:
            kb_uuid = _resolve_kb(c, kb_id)
            with purge_progress(output=output) as on_progress:
                res = c.knowledge_bases.purge(
                    kb_uuid,
                    strategy=strategy,  # type: ignore[arg-type]
                    on_progress=on_progress,
                )
            render(
                {
                    "indexes_processed": res.indexes_processed,
                    "documents_deleted": res.documents_deleted,
                    "errors": res.errors,
                    "per_index": [asdict(p) for p in res.per_index],
                },
                fmt=output,
            )

    _run(go)


# --- Index --------------------------------------------------------------------
@index_app.command("list")
def index_list(
    kb_ref: str,
    epoch: bool = EPOCH_OPT,
    output: str = OUTPUT_OPT,
) -> None:
    def go() -> None:
        with _client() as c:
            kb_uuid = _resolve_kb(c, kb_ref)
            rows = []
            for ix in c.indexes.list(kb_uuid).data:
                rows.append(
                    {
                        "id": ix.id,
                        "name": ix.name,
                        "status": getattr(ix.status, "value", ix.status),
                        "documents": getattr(ix, "num_documents", "—") or "—",
                        "embeddings_model_endpoint": ix.embeddings_model_endpoint,
                        "chunk_size": ix.chunk_size,
                        "last_indexed_at": _fmt_ts(
                            getattr(ix, "last_indexed_at", None), epoch=epoch
                        ),
                    }
                )
            render(
                rows,
                fmt=output,
                columns=[
                    "id",
                    "name",
                    "status",
                    "documents",
                    "embeddings_model_endpoint",
                    "chunk_size",
                    "last_indexed_at",
                ],
            )

    _run(go)


@index_app.command("create")
def index_create(
    kb_ref: str,
    name: str = typer.Option(...),
    embeddings_model: str = typer.Option(..., "--embeddings-model"),
    chunk_size: int = 400,
    chunk_overlap: int = 100,
    output: str = OUTPUT_OPT,
) -> None:
    def go() -> None:
        with _client() as c:
            kb_uuid = _resolve_kb(c, kb_ref)
            ix = c.indexes.create(
                kb_uuid,
                IndexCreate(
                    name=name,
                    embeddings_model_endpoint=embeddings_model,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                ),
            )
            render(ix, fmt=output)

    _run(go)


@index_app.command("upload")
def index_upload(kb_ref: str, index_ref: str, file: str, output: str = OUTPUT_OPT) -> None:
    def go() -> None:
        with _client() as c:
            kb_uuid, idx_uuid = _resolve_index(c, kb_ref, index_ref)
            doc = c.indexes.upload_document(kb_uuid, idx_uuid, file)
            render(doc, fmt=output)

    _run(go)


@index_app.command("search")
def index_search(
    kb_ref: str,
    index_ref: str,
    query: str,
    top_n: int = 5,
    similarity_cutoff: float = 0.0,
    output: str = OUTPUT_OPT,
) -> None:
    def go() -> None:
        with _client() as c:
            kb_uuid, idx_uuid = _resolve_index(c, kb_ref, index_ref)
            res = c.indexes.search(
                kb_uuid,
                idx_uuid,
                SearchQuery(query=query, top_n=top_n, similarity_cutoff=similarity_cutoff),
            )
            render(
                [h.model_dump(mode="json") for h in res.hits],
                fmt=output,
                columns=["score", "origin_name", "text"],
            )

    _run(go)


@index_app.command("wait")
def index_wait(
    kb_ref: str, index_ref: str, timeout: float = 300.0, output: str = OUTPUT_OPT
) -> None:
    def go() -> None:
        with _client() as c:
            kb_uuid, idx_uuid = _resolve_index(c, kb_ref, index_ref)
            indexing = c.indexes.wait_for_indexing(kb_uuid, idx_uuid, timeout=timeout, interval=1.0)
            render(indexing, fmt=output)

    _run(go)


@index_app.command("delete")
def index_delete(kb_ref: str, index_ref: str, yes: bool = YES_OPT) -> None:
    """Delete an index entirely (cascades documents)."""
    _confirm(f"delete index {index_ref} under KB {kb_ref}?", yes=yes)

    def go() -> None:
        with _client() as c:
            kb_uuid, idx_uuid = _resolve_index(c, kb_ref, index_ref)
            c.indexes.delete(kb_uuid, idx_uuid)
            typer.echo(f"deleted {index_ref}")

    _run(go)


@index_app.command("purge")
def index_purge(
    kb_ref: str,
    index_ref: str,
    yes: bool = YES_OPT,
    strategy: str = typer.Option("auto", "--strategy", help="auto | api | recreate"),
    output: str = OUTPUT_OPT,
) -> None:
    """Delete all documents in an index. Index itself stays (or is recreated)."""
    _confirm(f"purge all documents in index {index_ref}?", yes=yes)

    def go() -> None:
        from pais.cli._purge_progress import purge_progress

        with _client() as c:
            kb_uuid, idx_uuid = _resolve_index(c, kb_ref, index_ref)
            with purge_progress(output=output) as on_progress:
                res = c.indexes.purge(
                    kb_uuid,
                    idx_uuid,
                    strategy=strategy,  # type: ignore[arg-type]
                    on_progress=on_progress,
                )
            render(asdict(res), fmt=output)
            if res.new_index_id:
                typer.echo(
                    f"NOTE: index was recreated; new index_id={res.new_index_id} "
                    f"(update any agents referencing the old id)",
                    err=True,
                )

    _run(go)


@index_app.command("cancel")
def index_cancel(
    kb_ref: str,
    index_ref: str,
    yes: bool = YES_OPT,
    strategy: str = typer.Option("auto", "--strategy", help="auto | api | recreate"),
    output: str = OUTPUT_OPT,
) -> None:
    """Cancel an in-progress indexing job."""
    _confirm(f"cancel indexing for index {index_ref}?", yes=yes)

    def go() -> None:
        with _client() as c:
            kb_uuid, idx_uuid = _resolve_index(c, kb_ref, index_ref)
            res = c.indexes.cancel_indexing(kb_uuid, idx_uuid, strategy=strategy)  # type: ignore[arg-type]
            render(asdict(res), fmt=output)
            if res.new_index_id:
                typer.echo(
                    f"NOTE: index was recreated; new index_id={res.new_index_id}",
                    err=True,
                )

    _run(go)


# --- Agent --------------------------------------------------------------------
@agent_app.command("list")
def agent_list(output: str = OUTPUT_OPT) -> None:
    def go() -> None:
        with _client() as c:
            render(
                c.agents.list().data,
                fmt=output,
                columns=["id", "name", "model", "status"],
            )

    _run(go)


@agent_app.command("create")
def agent_create(
    name: str = typer.Option(...),
    model: str = typer.Option(...),
    instructions: str | None = typer.Option(None),
    index_id: str | None = typer.Option(
        None, "--index-id", help="Index UUID to link as the agent's KB source"
    ),
    index_top_n: int = typer.Option(
        5, "--index-top-n", help="How many chunks the agent retrieves per query"
    ),
    index_similarity_cutoff: float = typer.Option(
        0.0,
        "--index-similarity-cutoff",
        help="Minimum similarity score for retrieved chunks (0.0 disables filtering)",
    ),
    kb_search_tool: str | None = typer.Option(
        None,
        "--kb-search-tool",
        hidden=True,
        help="[legacy] MCP tool id of a KB-index-search tool; prefer --index-id",
    ),
    session_max_length: int | None = typer.Option(
        None,
        "--session-max-length",
        help="Max tokens the agent keeps in session history before summarization. "
        "Omit to use the server default.",
    ),
    session_summarization_strategy: str | None = typer.Option(
        None,
        "--session-summarization-strategy",
        help="How the agent trims session history (e.g. 'delete_oldest'). "
        "Omit to use the server default.",
    ),
    output: str = OUTPUT_OPT,
) -> None:
    def go() -> None:
        with _client() as c:
            tools: list[ToolLink] = []
            if kb_search_tool:
                tools.append(
                    ToolLink(
                        link_type=ToolLinkType.PAIS_KNOWLEDGE_BASE_INDEX_SEARCH_TOOL_LINK,
                        tool_id=kb_search_tool,
                        top_n=index_top_n,
                        similarity_cutoff=index_similarity_cutoff,
                    )
                )
            agent = c.agents.create(
                AgentCreate(
                    name=name,
                    model=model,
                    instructions=instructions,
                    index_id=index_id,
                    index_top_n=index_top_n if index_id else None,
                    index_similarity_cutoff=(index_similarity_cutoff if index_id else None),
                    tools=tools or None,
                    session_max_length=session_max_length,
                    session_summarization_strategy=session_summarization_strategy,
                )
            )
            render(agent, fmt=output)

    _run(go)


@agent_app.command("chat")
def agent_chat(
    agent_id: str,
    message: str | None = typer.Argument(None, help="User message. Omit when using --file."),
    file: Path | None = typer.Option(
        None, "--file", "-F", help="Read the user message from this file instead of the argument."
    ),
    output: str = OUTPUT_OPT,
) -> None:
    if (message is None) == (file is None):
        raise typer.BadParameter("Pass exactly one of MESSAGE (positional) or --file PATH.")
    content = file.expanduser().read_text(encoding="utf-8") if file is not None else message
    assert content is not None

    def go() -> None:
        with _client() as c:
            resp = c.agents.chat(
                agent_id,
                ChatCompletionRequest(messages=[ChatMessage(role="user", content=content)]),
            )
            if output == "table":
                typer.echo(resp.choices[0].message.content or "")
            else:
                render(resp, fmt=output)

    _run(go)


@agent_app.command("delete")
def agent_delete(agent_id: str) -> None:
    def go() -> None:
        with _client() as c:
            c.agents.delete(agent_id)
            typer.echo(f"deleted {agent_id}")

    _run(go)


# --- MCP + models -------------------------------------------------------------
@mcp_app.command("tools")
def mcp_tools(server: str = "built-in", output: str = OUTPUT_OPT) -> None:
    def go() -> None:
        with _client() as c:
            tools = c.mcp_tools.list(server=server).data
            render(tools, fmt=output, columns=["id", "name", "description"])

    _run(go)


@models_app.command("list")
def models_list(output: str = OUTPUT_OPT) -> None:
    def go() -> None:
        with _client() as c:
            render(
                c.models.list().data,
                fmt=output,
                columns=["id", "model_type", "model_engine"],
            )

    _run(go)


# --- Mock server --------------------------------------------------------------
@mock_app.command("serve")
def mock_serve(
    host: str = "127.0.0.1",
    port: int = 8080,
    seed: str | None = None,
) -> None:
    import uvicorn

    from pais_mock.server import build_app

    uvicorn.run(build_app(seed=seed), host=host, port=port, log_level="info")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
