"""Generic ingest runner: walk paths, run a Splitter, upload, report.

Splitter-agnostic — takes a Splitter instance and a file or directory.
Mirrors v0.3 `pais.dev.ingest` semantics (worker pool, JSON report,
per-file `--replace` matching) but works for any splitter kind.
"""

from __future__ import annotations

import io
import json
import statistics
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import IO

from pais.client import PaisClient
from pais.ingest.splitters._base import SplitDoc, Splitter
from pais.logging import get_logger, new_request_id
from pais.models import Document

_log = get_logger("pais.ingest.runner")


@dataclass
class FileResult:
    file: str
    group_key: str
    chunks_emitted: int
    chunks_uploaded: int
    document_ids: list[str] = field(default_factory=list)
    chunk_sizes: list[int] = field(default_factory=list)
    deleted_existing: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class IngestReport:
    files: list[FileResult] = field(default_factory=list)
    total_files: int = 0
    total_failed: int = 0
    total_chunks_uploaded: int = 0
    total_existing_deleted: int = 0
    chunk_size_distribution: dict[str, int] = field(default_factory=dict)
    splitter_kind: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": {
                "splitter_kind": self.splitter_kind,
                "total_files": self.total_files,
                "total_failed": self.total_failed,
                "total_chunks_uploaded": self.total_chunks_uploaded,
                "total_existing_deleted": self.total_existing_deleted,
                "chunk_size_distribution": self.chunk_size_distribution,
            },
            "files": [asdict(f) for f in self.files],
        }


def collect_files(root: Path) -> list[Path]:
    """Return a sorted list of input files. Single file → [file]; dir → all files recursively."""
    if root.is_file():
        return [root]
    return sorted(p for p in root.rglob("*") if p.is_file())


def ingest_path(
    client: PaisClient,
    root: Path,
    *,
    splitter: Splitter,
    kb_id: str,
    index_id: str,
    workers: int = 4,
    replace: bool = False,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> IngestReport:
    """Run `splitter` over every file under `root` and upload the results.

    `replace=True`: before uploading each file's chunks, delete existing docs
    in the index whose `origin_name` starts with `splitter.group_key(file)`.
    `dry_run=True`: split locally + report; no DELETE, no POST.
    """
    files = collect_files(root)
    report = IngestReport(total_files=len(files), splitter_kind=type(splitter).kind)
    request_id = new_request_id()
    lock = threading.Lock()
    _log.info(
        "pais.ingest.start",
        root=str(root),
        files=len(files),
        splitter=type(splitter).kind,
        replace=replace,
        dry_run=dry_run,
        request_id=request_id,
    )

    def worker(p: Path) -> FileResult:
        try:
            return _process_one(
                client,
                p,
                splitter=splitter,
                kb_id=kb_id,
                index_id=index_id,
                replace=replace,
                dry_run=dry_run,
            )
        except Exception as e:  # pragma: no cover - defensive
            return FileResult(
                file=str(p),
                group_key="",
                chunks_emitted=0,
                chunks_uploaded=0,
                errors=[f"{type(e).__name__}: {e}"],
            )
        finally:
            if progress is not None:
                progress(str(p))

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(worker, p) for p in files]
        for fut in as_completed(futures):
            res = fut.result()
            with lock:
                report.files.append(res)
                report.total_chunks_uploaded += res.chunks_uploaded
                report.total_existing_deleted += res.deleted_existing
                if res.errors or (res.chunks_emitted and res.chunks_uploaded == 0 and not dry_run):
                    report.total_failed += 1

    sizes: list[int] = []
    for f in report.files:
        sizes.extend(f.chunk_sizes)
    if sizes:
        sizes.sort()
        report.chunk_size_distribution = {
            "min": sizes[0],
            "p50": int(statistics.median(sizes)),
            "p95": sizes[int(0.95 * (len(sizes) - 1))],
            "max": sizes[-1],
            "count": len(sizes),
        }

    _log.info(
        "pais.ingest.done",
        total_files=report.total_files,
        total_failed=report.total_failed,
        total_chunks_uploaded=report.total_chunks_uploaded,
        request_id=request_id,
    )
    return report


def write_report(report: IngestReport, out_path: Path) -> Path:
    out_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return out_path


# -------------------- internals --------------------


def _process_one(
    client: PaisClient,
    path: Path,
    *,
    splitter: Splitter,
    kb_id: str,
    index_id: str,
    replace: bool,
    dry_run: bool,
) -> FileResult:
    group_key = splitter.group_key(path)
    result = FileResult(file=str(path), group_key=group_key, chunks_emitted=0, chunks_uploaded=0)

    if replace and not dry_run:
        # Each splitter is responsible for returning a group_key that's a valid
        # prefix for its own origin_names — runner doesn't add `__` automatically.
        prefix = group_key
        try:
            pr = client.indexes.purge(kb_id, index_id, strategy="api", match_origin_prefix=prefix)
            result.deleted_existing = pr.documents_deleted
            if pr.errors:
                result.errors.extend(pr.errors)
        except Exception as e:
            result.errors.append(f"replace: {type(e).__name__}: {e}")

    docs: list[SplitDoc] = list(splitter.split(path))
    result.chunks_emitted = len(docs)
    for d in docs:
        result.chunk_sizes.append(len(d.body))
        if dry_run:
            continue
        try:
            doc = _upload_one(client, kb_id, index_id, d)
            result.document_ids.append(doc.id)
            result.chunks_uploaded += 1
        except Exception as e:
            result.errors.append(f"{d.origin_name}: {type(e).__name__}: {e}")
    return result


def _upload_one(client: PaisClient, kb_id: str, index_id: str, doc: SplitDoc) -> Document:
    files: dict[str, tuple[str, IO[bytes], str]] = {
        "file": (doc.origin_name, io.BytesIO(doc.body), doc.media_type)
    }
    resp = client._transport.request(
        "POST",
        f"/control/knowledge-bases/{kb_id}/indexes/{index_id}/documents",
        files=files,
    )
    return Document.model_validate(resp.body)
