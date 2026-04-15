# Changelog

## [Unreleased] — 0.2.0 · test-suite ingestion pipeline

### Added
- `pais-dev` CLI (`pais.cli.dev`) with `split-suite`, `ingest-suite`, `ingest-suites` commands.
- `pais.dev` package:
  - `pais.dev.markdown` — heading-aware markdown parser (H1/H2/H3, tolerates fenced code blocks).
  - `pais.dev.token_budget` — `token_count()` + `BUDGET=400` backed by `BAAI/bge-small-en-v1.5` via HuggingFace `tokenizers`.
  - `pais.dev.split_suite` — splits one test-suite markdown into per-section files; breadcrumb header (`# Suite / ## Section / ## Kind`); slug-sanitized filenames; paragraph + sentence sub-split; `SectionTooLargeError` on indivisible overflow.
  - `pais.dev.ingest` — `ingest_file` + `ingest_directory` with worker pool, progress callbacks, JSON report (per-suite + token distribution footer), ingest-time budget re-check.
- `tokenizers>=0.15` added to the `[dev]` optional-dependency group.
- Docs: [`docs/ingestion.md`](docs/ingestion.md) (design + troubleshooting), [`docs/architecture.md`](docs/architecture.md) (layer map).
- Tests: `test_markdown_parser`, `test_token_budget`, `test_split_suite`, `test_ingest`, `test_cli_dev` (+ optional real-fixture test gated on `~/Downloads/Access-Management.md`).
- README gains an **Ingest test suites** section with a 4-command runbook, content-hygiene warning, and idempotency note.

### Changed
- `tests/conftest.py` now auto-clears stdlib logging handlers between tests to avoid stale-stream leaks interfering with `CliRunner` output.

### Known limitations
- Re-running `ingest-suites` against the same directory creates duplicate documents in PAIS (no dedupe on `origin_name`). Planned: `--replace` flag.

## 0.1.0 — SDK foundation

### Added
- Contract-first Python SDK for VMware Private AI Service (KB / Index / Agent / OpenAI-compat + MCP tools).
- Pluggable auth: `none` (internal-network default), `bearer`, `oidc_password` (with token cache at `~/.pais/token.json` mode 0600).
- Transport layer with retries, request-id propagation, 502 cold-start retries on chat completions, self-signed TLS toggle.
- Bundled mock server (`python -m pais_mock`) and in-process fake transport, both backed by a shared `Store` so tests and the server speak the same contract.
- `pais` CLI with `--output {table,json,yaml}` and structured exit codes (0 success / 1 user error / 2 API error / 3 auth error).
- Structured JSON logging with secret redaction and request-id contextvar.
- 69 tests, ≥ 90 % coverage on `src/pais/`.
- CI matrix on Python 3.11 + 3.12.
