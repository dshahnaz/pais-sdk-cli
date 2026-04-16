"""`pais` CLI entrypoint."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

import typer

from pais.cli import config_cmd
from pais.cli._output import exit_code_for, render
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

app = typer.Typer(help="PAIS SDK + CLI — talk to VMware Private AI Service or a local mock.")
kb_app = typer.Typer(help="Knowledge Base commands")
index_app = typer.Typer(help="Index commands (nested under a KB)")
agent_app = typer.Typer(help="Agent commands")
mcp_app = typer.Typer(help="MCP tool discovery")
models_app = typer.Typer(help="Model discovery")
mock_app = typer.Typer(help="Run the local PAIS mock server")
app.add_typer(kb_app, name="kb")
app.add_typer(index_app, name="index")
app.add_typer(agent_app, name="agent")
app.add_typer(mcp_app, name="mcp")
app.add_typer(models_app, name="models")
app.add_typer(mock_app, name="mock")
app.add_typer(config_cmd.app, name="config")

_OutputOpt = typer.Option("table", "--output", "-o", help="table | json | yaml")
_YesOpt = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt")


def _print_version_and_exit(value: bool) -> None:
    if value:
        from pais import __version__

        typer.echo(f"pais {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    config: Path | None = typer.Option(
        None, "--config", help="Path to a TOML config file (overrides discovery)"
    ),
    profile: str | None = typer.Option(
        None, "--profile", help="Profile name within the config file"
    ),
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        is_eager=True,
        callback=_print_version_and_exit,
        help="Show version and exit",
    ),
) -> None:
    """Pin --config / --profile so every subcommand's Settings() picks them up."""
    set_runtime_overrides(config_path=config, profile=profile)
    # Eager validation: surface config-file errors here with a clean message
    # rather than letting them bubble up as a Python traceback later.
    from pais.cli._config_file import ConfigError, load_profile

    try:
        load_profile(path=config, profile=profile)
    except ConfigError as e:
        typer.echo(f"config error: {e}", err=True)
        raise typer.Exit(code=1) from e


def _client() -> PaisClient:
    return Settings().build_client()


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
def kb_list(output: str = _OutputOpt) -> None:
    def go() -> None:
        with _client() as c:
            items = c.knowledge_bases.list().data
            render(
                items,
                fmt=output,
                columns=["id", "name", "data_origin_type", "created_at"],
            )

    _run(go)


@kb_app.command("create")
def kb_create(
    name: str = typer.Option(...),
    description: str | None = typer.Option(None),
    output: str = _OutputOpt,
) -> None:
    def go() -> None:
        with _client() as c:
            kb = c.knowledge_bases.create(KnowledgeBaseCreate(name=name, description=description))
            render(kb, fmt=output)

    _run(go)


@kb_app.command("get")
def kb_get(kb_id: str, output: str = _OutputOpt) -> None:
    def go() -> None:
        with _client() as c:
            render(c.knowledge_bases.get(kb_id), fmt=output)

    _run(go)


@kb_app.command("delete")
def kb_delete(kb_id: str, yes: bool = _YesOpt) -> None:
    """Delete a KB (cascades indexes + documents)."""
    _confirm(f"delete KB {kb_id} and all its indexes/documents?", yes=yes)

    def go() -> None:
        with _client() as c:
            c.knowledge_bases.delete(kb_id)
            typer.echo(f"deleted {kb_id}")

    _run(go)


@kb_app.command("purge")
def kb_purge(
    kb_id: str,
    yes: bool = _YesOpt,
    strategy: str = typer.Option("auto", "--strategy", help="auto | api | recreate"),
    output: str = _OutputOpt,
) -> None:
    """Delete every document in every index under the KB. KB itself stays."""
    _confirm(f"purge all documents under KB {kb_id}?", yes=yes)

    def go() -> None:
        with _client() as c:
            res = c.knowledge_bases.purge(kb_id, strategy=strategy)  # type: ignore[arg-type]
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
def index_list(kb_id: str, output: str = _OutputOpt) -> None:
    def go() -> None:
        with _client() as c:
            render(
                c.indexes.list(kb_id).data,
                fmt=output,
                columns=["id", "name", "status", "embeddings_model_endpoint"],
            )

    _run(go)


@index_app.command("create")
def index_create(
    kb_id: str,
    name: str = typer.Option(...),
    embeddings_model: str = typer.Option(..., "--embeddings-model"),
    chunk_size: int = 400,
    chunk_overlap: int = 100,
    output: str = _OutputOpt,
) -> None:
    def go() -> None:
        with _client() as c:
            ix = c.indexes.create(
                kb_id,
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
def index_upload(kb_id: str, index_id: str, file: str, output: str = _OutputOpt) -> None:
    def go() -> None:
        with _client() as c:
            doc = c.indexes.upload_document(kb_id, index_id, file)
            render(doc, fmt=output)

    _run(go)


@index_app.command("search")
def index_search(
    kb_id: str,
    index_id: str,
    query: str,
    top_n: int = 5,
    similarity_cutoff: float = 0.0,
    output: str = _OutputOpt,
) -> None:
    def go() -> None:
        with _client() as c:
            res = c.indexes.search(
                kb_id,
                index_id,
                SearchQuery(query=query, top_n=top_n, similarity_cutoff=similarity_cutoff),
            )
            render(
                [h.model_dump(mode="json") for h in res.hits],
                fmt=output,
                columns=["score", "origin_name", "text"],
            )

    _run(go)


@index_app.command("wait")
def index_wait(kb_id: str, index_id: str, timeout: float = 300.0, output: str = _OutputOpt) -> None:
    def go() -> None:
        with _client() as c:
            indexing = c.indexes.wait_for_indexing(kb_id, index_id, timeout=timeout, interval=1.0)
            render(indexing, fmt=output)

    _run(go)


@index_app.command("delete")
def index_delete(kb_id: str, index_id: str, yes: bool = _YesOpt) -> None:
    """Delete an index entirely (cascades documents)."""
    _confirm(f"delete index {index_id} under KB {kb_id}?", yes=yes)

    def go() -> None:
        with _client() as c:
            c.indexes.delete(kb_id, index_id)
            typer.echo(f"deleted {index_id}")

    _run(go)


@index_app.command("purge")
def index_purge(
    kb_id: str,
    index_id: str,
    yes: bool = _YesOpt,
    strategy: str = typer.Option("auto", "--strategy", help="auto | api | recreate"),
    output: str = _OutputOpt,
) -> None:
    """Delete all documents in an index. Index itself stays (or is recreated)."""
    _confirm(f"purge all documents in index {index_id}?", yes=yes)

    def go() -> None:
        with _client() as c:
            res = c.indexes.purge(kb_id, index_id, strategy=strategy)  # type: ignore[arg-type]
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
    kb_id: str,
    index_id: str,
    yes: bool = _YesOpt,
    strategy: str = typer.Option("auto", "--strategy", help="auto | api | recreate"),
    output: str = _OutputOpt,
) -> None:
    """Cancel an in-progress indexing job."""
    _confirm(f"cancel indexing for index {index_id}?", yes=yes)

    def go() -> None:
        with _client() as c:
            res = c.indexes.cancel_indexing(kb_id, index_id, strategy=strategy)  # type: ignore[arg-type]
            render(asdict(res), fmt=output)
            if res.new_index_id:
                typer.echo(
                    f"NOTE: index was recreated; new index_id={res.new_index_id}",
                    err=True,
                )

    _run(go)


# --- Agent --------------------------------------------------------------------
@agent_app.command("list")
def agent_list(output: str = _OutputOpt) -> None:
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
    kb_search_tool: str | None = typer.Option(
        None, "--kb-search-tool", help="MCP tool id of a KB-index-search tool to link"
    ),
    top_n: int = 5,
    similarity_cutoff: float = 0.0,
    output: str = _OutputOpt,
) -> None:
    def go() -> None:
        with _client() as c:
            tools: list[ToolLink] = []
            if kb_search_tool:
                tools.append(
                    ToolLink(
                        link_type=ToolLinkType.PAIS_KNOWLEDGE_BASE_INDEX_SEARCH_TOOL_LINK,
                        tool_id=kb_search_tool,
                        top_n=top_n,
                        similarity_cutoff=similarity_cutoff,
                    )
                )
            agent = c.agents.create(
                AgentCreate(name=name, model=model, instructions=instructions, tools=tools)
            )
            render(agent, fmt=output)

    _run(go)


@agent_app.command("chat")
def agent_chat(agent_id: str, message: str, output: str = _OutputOpt) -> None:
    def go() -> None:
        with _client() as c:
            resp = c.agents.chat(
                agent_id,
                ChatCompletionRequest(messages=[ChatMessage(role="user", content=message)]),
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
def mcp_tools(server: str = "built-in", output: str = _OutputOpt) -> None:
    def go() -> None:
        with _client() as c:
            tools = c.mcp_tools.list(server=server).data
            render(tools, fmt=output, columns=["id", "name", "description"])

    _run(go)


@models_app.command("list")
def models_list(output: str = _OutputOpt) -> None:
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
