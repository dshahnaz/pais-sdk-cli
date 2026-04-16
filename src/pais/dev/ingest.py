"""Split + upload test-suite markdown files to a PAIS index.

Two entry points: `ingest_file` for one suite, `ingest_directory` for
bulk. The batch path uses a worker pool, retries via the SDK transport,
and writes a JSON report with a per-suite breakdown + token-count
distribution footer.
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
from pais.dev.split_suite import SplitSection, split_suite
from pais.dev.token_budget import BUDGET, token_count
from pais.logging import get_logger, new_request_id
from pais.models import Document

_log = get_logger("pais.dev.ingest")


@dataclass
class SuiteResult:
    suite_name: str
    file: str
    sections_emitted: int
    sections_uploaded: int
    document_ids: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    token_counts: list[int] = field(default_factory=list)


@dataclass
class IngestReport:
    suites: list[SuiteResult] = field(default_factory=list)
    token_distribution: dict[str, int] = field(default_factory=dict)
    total_sections_emitted: int = 0
    total_sections_uploaded: int = 0
    total_suites: int = 0
    total_suites_failed: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "summary": {
                "total_suites": self.total_suites,
                "total_suites_failed": self.total_suites_failed,
                "total_sections_emitted": self.total_sections_emitted,
                "total_sections_uploaded": self.total_sections_uploaded,
                "token_distribution": self.token_distribution,
            },
            "suites": [asdict(s) for s in self.suites],
        }


def ingest_file(
    client: PaisClient,
    md_path: str | Path,
    *,
    kb_id: str,
    index_id: str,
) -> SuiteResult:
    """Split one suite markdown file and upload every section to PAIS."""
    path = Path(md_path)
    sections = split_suite(path)
    _guard_budget(sections)
    return _upload_sections(client, path, sections, kb_id=kb_id, index_id=index_id)


def ingest_directory(
    client: PaisClient,
    root: str | Path,
    *,
    kb_id: str,
    index_id: str,
    workers: int = 4,
    progress: Callable[[str], None] | None = None,
    replace: bool = False,
) -> IngestReport:
    """Walk `root` for .md suite files, split and upload each in parallel.

    When ``replace=True``, before uploading each suite we delete any existing
    documents in the index whose ``origin_name`` starts with the suite slug
    (the part of the filename before the first ``__``). Untouched suites stay.
    """
    md_files = sorted(Path(root).rglob("*.md"))
    report = IngestReport(total_suites=len(md_files))
    lock = threading.Lock()
    request_id = new_request_id()
    _log.info(
        "pais.ingest.start",
        root=str(root),
        files=len(md_files),
        replace=replace,
        request_id=request_id,
    )

    def worker(p: Path) -> SuiteResult:
        try:
            if replace:
                _replace_for_suite(client, p, kb_id=kb_id, index_id=index_id)
            result = ingest_file(client, p, kb_id=kb_id, index_id=index_id)
        except Exception as e:
            result = SuiteResult(
                suite_name=p.stem,
                file=str(p),
                sections_emitted=0,
                sections_uploaded=0,
                errors=[f"{type(e).__name__}: {e}"],
            )
        if progress is not None:
            progress(str(p))
        return result

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(worker, p) for p in md_files]
        for fut in as_completed(futures):
            res = fut.result()
            with lock:
                report.suites.append(res)
                report.total_sections_emitted += res.sections_emitted
                report.total_sections_uploaded += res.sections_uploaded
                if res.errors or res.sections_uploaded == 0:
                    report.total_suites_failed += 1

    all_counts: list[int] = []
    for s in report.suites:
        all_counts.extend(s.token_counts)
    if all_counts:
        all_counts.sort()
        p50 = int(statistics.median(all_counts))
        p95 = all_counts[int(0.95 * (len(all_counts) - 1))]
        report.token_distribution = {
            "min": all_counts[0],
            "p50": p50,
            "p95": p95,
            "max": all_counts[-1],
            "budget": BUDGET,
        }

    _log.info(
        "pais.ingest.done",
        total_suites=report.total_suites,
        total_suites_failed=report.total_suites_failed,
        total_sections_uploaded=report.total_sections_uploaded,
        request_id=request_id,
    )
    return report


def write_report(report: IngestReport, out_path: str | Path) -> Path:
    p = Path(out_path)
    p.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return p


# -------------------- internals --------------------


def _guard_budget(sections: list[SplitSection]) -> None:
    """Ingest-time re-check: every section must be <= BUDGET tokens."""
    for s in sections:
        n = token_count(s.rendered)
        if n > BUDGET:
            raise ValueError(
                f"{s.suite_name}:{s.section_name} is {n} tokens, exceeds budget {BUDGET}"
            )


def _upload_sections(
    client: PaisClient,
    path: Path,
    sections: list[SplitSection],
    *,
    kb_id: str,
    index_id: str,
) -> SuiteResult:
    suite_name = sections[0].suite_name if sections else path.stem
    result = SuiteResult(
        suite_name=suite_name,
        file=str(path),
        sections_emitted=len(sections),
        sections_uploaded=0,
    )
    for s in sections:
        n = token_count(s.rendered)
        result.token_counts.append(n)
        try:
            doc = _upload_one(client, kb_id, index_id, s)
            result.document_ids.append(doc.id)
            result.sections_uploaded += 1
        except Exception as e:
            msg = f"{s.filename}: {type(e).__name__}: {e}"
            result.errors.append(msg)
            _log.warning(
                "pais.ingest.upload_failed", suite=suite_name, section=s.section_name, error=str(e)
            )
    return result


def _suite_slug_from_path(path: Path) -> str:
    """Compute the same suite slug the splitter uses, so --replace can match
    `origin_name` prefixes without re-running the splitter."""
    from pais.dev.markdown import parse
    from pais.dev.split_suite import _slugify  # internal but stable

    doc = parse(path.read_text(encoding="utf-8"))
    return _slugify(doc.title or path.stem)


def _replace_for_suite(
    client: PaisClient,
    path: Path,
    *,
    kb_id: str,
    index_id: str,
) -> None:
    """Delete documents in the index whose origin_name matches this suite."""
    from pais.resources.indexes import IndexesResource

    slug = _suite_slug_from_path(path)
    prefix = f"{slug}__"
    indexes = IndexesResource(client._transport)
    pr = indexes.purge(kb_id, index_id, strategy="api", match_origin_prefix=prefix)
    _log.info(
        "pais.ingest.replace",
        suite=slug,
        deleted=pr.documents_deleted,
        errors=len(pr.errors),
    )


def _upload_one(client: PaisClient, kb_id: str, index_id: str, section: SplitSection) -> Document:
    """Upload one SplitSection as an in-memory multipart file (no temp file)."""
    buf = io.BytesIO(section.rendered.encode("utf-8"))
    files: dict[str, tuple[str, IO[bytes], str]] = {
        "file": (section.filename, buf, "text/markdown")
    }
    resp = client._transport.request(
        "POST",
        f"/control/knowledge-bases/{kb_id}/indexes/{index_id}/documents",
        files=files,
    )
    return Document.model_validate(resp.body)
