"""`pais kb ensure` — make PAIS match the declarative TOML config."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

import typer

from pais.cli import _alias
from pais.cli._config_file import load_profile_config
from pais.cli._flags import DRY_RUN_OPT, OUTPUT_OPT, YES_OPT
from pais.cli._output import exit_code_for, render
from pais.cli._profile_config import IndexDeclaration, KnowledgeBaseDeclaration, ProfileConfig
from pais.client import PaisClient
from pais.config import Settings
from pais.errors import PaisError
from pais.logging import get_logger
from pais.models import (
    DataOriginType,
    Index,
    IndexCreate,
    KnowledgeBaseCreate,
    TextSplittingKind,
)

_log = get_logger("pais.kb.ensure")


@dataclass
class EnsureRow:
    kind: str  # "kb" or "index"
    alias: str  # e.g. "test_suites" or "test_suites:main"
    name: str
    action: str  # "existing" | "created" | "would-create" | "mismatch" | "pruned" | "would-prune" | "skipped"
    detail: str = ""
    uuid: str | None = None


@dataclass
class EnsureReport:
    rows: list[EnsureRow] = field(default_factory=list)
    profile: str = ""
    dry_run: bool = False
    pruned: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "profile": self.profile,
            "dry_run": self.dry_run,
            "pruned": self.pruned,
            "rows": [asdict(r) for r in self.rows],
        }


def _client() -> PaisClient:
    return Settings().build_client()


def _run(fn: Callable[[], None]) -> None:
    try:
        fn()
    except PaisError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(code=exit_code_for(e)) from e
    except typer.BadParameter:
        raise
    except Exception as e:
        typer.echo(f"error: {type(e).__name__}: {e}", err=True)
        raise typer.Exit(code=1) from e


def _confirm_prune(item_label: str) -> bool:
    if not sys.stdin.isatty():
        typer.echo(f"refusing to prune {item_label} non-interactively (re-run with TTY)", err=True)
        return False
    return typer.confirm(f"prune {item_label}?", default=False)


def kb_ensure(
    dry_run: bool = DRY_RUN_OPT,
    prune: bool = typer.Option(
        False, "--prune", help="Also delete server-side KBs/indexes not in the TOML."
    ),
    yes: bool = YES_OPT,
    output: str = OUTPUT_OPT,
) -> None:
    """Materialize the declarative knowledge_bases / indexes block from the active profile.

    Idempotent. Re-running is safe. PAIS doesn't expose update for some
    fields — those mismatches are warned about, never silently mutated.
    """
    if prune and not yes:
        raise typer.BadParameter("--prune requires --yes")

    def go() -> None:
        cfg, _, profile = load_profile_config()
        if not cfg.knowledge_bases:
            typer.echo(
                "no [profiles.X.knowledge_bases.*] declared in the active profile — nothing to ensure",
                err=True,
            )
            raise typer.Exit(code=1)

        report = EnsureReport(profile=profile, dry_run=dry_run, pruned=prune)
        with _client() as c:
            _ensure_for_profile(c, cfg, report=report, dry_run=dry_run, prune=prune)

        # Refresh alias cache: drop entries for this profile, force re-resolution next time.
        _alias.clear_cache(profile=profile)

        render(report.to_dict(), fmt=output)
        if any(r.action == "mismatch" for r in report.rows):
            raise typer.Exit(code=1)

    _run(go)


def _ensure_for_profile(
    client: PaisClient,
    cfg: ProfileConfig,
    *,
    report: EnsureReport,
    dry_run: bool,
    prune: bool,
) -> None:
    server_kbs = client.knowledge_bases.list().data
    server_kb_by_name = {k.name: k for k in server_kbs}
    declared_kb_names: set[str] = set()

    for alias, kb_decl in cfg.knowledge_bases.items():
        declared_kb_names.add(kb_decl.name)
        existing = server_kb_by_name.get(kb_decl.name)
        if existing:
            kb_uuid = existing.id
            report.rows.append(
                EnsureRow(
                    kind="kb", alias=alias, name=kb_decl.name, action="existing", uuid=kb_uuid
                )
            )
        elif dry_run:
            report.rows.append(
                EnsureRow(kind="kb", alias=alias, name=kb_decl.name, action="would-create")
            )
            kb_uuid = ""  # no UUID yet
        else:
            created = client.knowledge_bases.create(
                KnowledgeBaseCreate(
                    name=kb_decl.name,
                    description=kb_decl.description,
                    data_origin_type=DataOriginType(kb_decl.data_origin_type),
                )
            )
            kb_uuid = created.id
            report.rows.append(
                EnsureRow(kind="kb", alias=alias, name=kb_decl.name, action="created", uuid=kb_uuid)
            )

        if not kb_uuid:
            # dry-run created nothing; can't list indexes under it
            for ix in kb_decl.indexes:
                report.rows.append(
                    EnsureRow(
                        kind="index",
                        alias=f"{alias}:{ix.alias}",
                        name=ix.name,
                        action="would-create",
                    )
                )
            continue

        _ensure_indexes(
            client,
            kb_alias=alias,
            kb_uuid=kb_uuid,
            kb_decl=kb_decl,
            report=report,
            dry_run=dry_run,
            prune=prune,
        )

    if prune:
        for server_kb in server_kbs:
            if server_kb.name in declared_kb_names:
                continue
            label = f"KB {server_kb.name!r} (uuid={server_kb.id})"
            if dry_run:
                report.rows.append(
                    EnsureRow(
                        kind="kb",
                        alias="—",
                        name=server_kb.name,
                        action="would-prune",
                        uuid=server_kb.id,
                    )
                )
                continue
            if _confirm_prune(label):
                client.knowledge_bases.delete(server_kb.id)
                report.rows.append(
                    EnsureRow(
                        kind="kb",
                        alias="—",
                        name=server_kb.name,
                        action="pruned",
                        uuid=server_kb.id,
                    )
                )
            else:
                report.rows.append(
                    EnsureRow(
                        kind="kb",
                        alias="—",
                        name=server_kb.name,
                        action="skipped",
                        uuid=server_kb.id,
                        detail="user declined prune",
                    )
                )


def _ensure_indexes(
    client: PaisClient,
    *,
    kb_alias: str,
    kb_uuid: str,
    kb_decl: KnowledgeBaseDeclaration,
    report: EnsureReport,
    dry_run: bool,
    prune: bool,
) -> None:
    server_indexes = client.indexes.list(kb_uuid).data
    server_ix_by_name = {i.name: i for i in server_indexes}
    declared_ix_names: set[str] = set()

    for ix_decl in kb_decl.indexes:
        declared_ix_names.add(ix_decl.name)
        existing = server_ix_by_name.get(ix_decl.name)
        full_alias = f"{kb_alias}:{ix_decl.alias}"
        if existing:
            mismatch = _diff_index(ix_decl, existing)
            if mismatch:
                report.rows.append(
                    EnsureRow(
                        kind="index",
                        alias=full_alias,
                        name=ix_decl.name,
                        action="mismatch",
                        detail=mismatch,
                        uuid=existing.id,
                    )
                )
            else:
                report.rows.append(
                    EnsureRow(
                        kind="index",
                        alias=full_alias,
                        name=ix_decl.name,
                        action="existing",
                        uuid=existing.id,
                    )
                )
        elif dry_run:
            report.rows.append(
                EnsureRow(kind="index", alias=full_alias, name=ix_decl.name, action="would-create")
            )
        else:
            created = client.indexes.create(
                kb_uuid,
                IndexCreate(
                    name=ix_decl.name,
                    description=ix_decl.description,
                    embeddings_model_endpoint=ix_decl.embeddings_model_endpoint,
                    text_splitting=TextSplittingKind(ix_decl.text_splitting),
                    chunk_size=ix_decl.chunk_size,
                    chunk_overlap=ix_decl.chunk_overlap,
                ),
            )
            report.rows.append(
                EnsureRow(
                    kind="index",
                    alias=full_alias,
                    name=ix_decl.name,
                    action="created",
                    uuid=created.id,
                )
            )

    if prune:
        for s_ix in server_indexes:
            if s_ix.name in declared_ix_names:
                continue
            label = f"index {s_ix.name!r} under KB {kb_alias} (uuid={s_ix.id})"
            if dry_run:
                report.rows.append(
                    EnsureRow(
                        kind="index", alias="—", name=s_ix.name, action="would-prune", uuid=s_ix.id
                    )
                )
                continue
            if _confirm_prune(label):
                client.indexes.delete(kb_uuid, s_ix.id)
                report.rows.append(
                    EnsureRow(
                        kind="index", alias="—", name=s_ix.name, action="pruned", uuid=s_ix.id
                    )
                )
            else:
                report.rows.append(
                    EnsureRow(
                        kind="index",
                        alias="—",
                        name=s_ix.name,
                        action="skipped",
                        uuid=s_ix.id,
                        detail="user declined prune",
                    )
                )


def _diff_index(decl: IndexDeclaration, server: Index) -> str:
    """Compare declared vs server index. Return diff string if mismatch, else ''."""
    issues: list[str] = []
    if decl.embeddings_model_endpoint != server.embeddings_model_endpoint:
        issues.append(
            f"embeddings_model_endpoint TOML={decl.embeddings_model_endpoint!r} server={server.embeddings_model_endpoint!r}"
        )
    if decl.chunk_size != server.chunk_size:
        issues.append(f"chunk_size TOML={decl.chunk_size} server={server.chunk_size}")
    if decl.chunk_overlap != server.chunk_overlap:
        issues.append(f"chunk_overlap TOML={decl.chunk_overlap} server={server.chunk_overlap}")
    return "; ".join(issues)
