# Changelog

## 0.4.1 · `pais status` + global short flags

Purely additive follow-on to 0.4.0. No breaking changes.

### Added
- **`pais status`** — one-shot environment overview. Renders the active profile (mode, base_url, auth, verify_ssl), a server reachability check, the alias cache state, KBs and (with `-c`) indexes with doc counts, and a drift section showing what differs between the TOML and the live server. Flags: `-c/--with-counts`, `-e/--epoch`, `--no-ping`, `-o/--output table|json|yaml`. JSON emits one machine-readable payload covering every section.
- **Global short-flag aliases.** Every shared option now exposes a single-letter form: `-h` (help, wired on every subcommand), `-p` (--profile), `-o` (--output), `-y` (--yes), `-n` (--dry-run), `-v` (--verbose), `-w` (--workers), `-r` (--replace), `-R` (--report), `-s` (--splitter), `-c` (--with-counts), `-e` (--epoch), `-f` (--force). `--prune` and `--no-ping` intentionally have no short form.

## 0.4.0 · unified `pais` CLI, generic ingest, declarative indexes

### ⚠️ Breaking changes
- **`pais-dev` is removed.** Its commands (`split-suite`, `ingest-suite`, `ingest-suites`) merged into a single generic `pais ingest <kb_ref>:<index_ref> <path>`. The old `pais-dev` console script ships as a redirect shim that exits 1 with a one-line migration hint; it will be removed entirely in v0.5. See [`docs/migration-0.3-to-0.4.md`](docs/migration-0.3-to-0.4.md).

### Added
- **Generic `pais ingest <kb_ref>:<index_ref> <path>`** picks the splitter from the index's TOML config (or `--splitter <kind>` to override). Supports `--replace`, `--dry-run`, `--workers`, `--report`.
- **Splitter registry with 4 built-ins**: `test_suite_md` (existing v0.3 behavior), `markdown_headings` (generic), `passthrough` (no transform), `text_chunks` (sliding-window for plain text).
- **Declarative KB/index/splitter blocks in TOML** under `[profiles.X.knowledge_bases.<alias>]` + `[[indexes]]` + `[indexes.splitter]`. Validated at load time with pydantic; errors point at the exact TOML path.
- **Alias system** — short names instead of UUIDs. Cache at `~/.pais/aliases.json`, 404-invalidation. Every existing UUID-taking command now also accepts an alias.
- **`pais kb ensure`** — idempotent; creates KBs/indexes declared in TOML that don't exist on the server. `--dry-run` previews; `--prune --yes` deletes server-side resources not in TOML (per-item confirmation).
- **`pais kb show <alias|uuid>`** — full detail view with per-index breakdown.
- **`pais kb list --with-counts`** opt-in flag adds `indexes` and `documents` columns. Default columns now include `description` and `updated`.
- **Human dates by default** on `kb list` / `kb show` / `index list`. `--epoch` opts out.
- **`pais splitters list / show <kind>`** — discover splitters and their option schemas.
- **`pais alias list / clear [<alias>]`** — inspect / invalidate the resolution cache.

### Verified PAIS API constraints (drove the design)
- API still doesn't expose cancel-indexing, per-document DELETE, or batch DELETE — cleanup ops continue using the v0.3 probe-then-fallback pattern.

### Migration
| v0.3 | v0.4 |
|---|---|
| `pais-dev split-suite f.md --out d/` | `pais ingest K:I f.md --dry-run` (writes to report instead) |
| `pais-dev ingest-suite f.md --kb K --index I` | `pais ingest K:I f.md` |
| `pais-dev ingest-suites d/ --kb K --index I` | `pais ingest K:I d/` |
| `pais-dev ingest-suites d/ --kb K --index I --replace` | `pais ingest K:I d/ --replace` |

## 0.3.1

### Fixed
- `pais` and any subcommand now print a clean `config error: ...` message and exit 1 when the discovered TOML config file is invalid, instead of dumping a Python traceback.

## 0.3.0 · config file, cleanup ops, cancel-indexing

### Added
- **Persistent config file** (`~/.pais/config.toml` or `./pais.toml` — project wins) with `[profiles.<name>]` tables. Loaded by `Settings` so every command picks it up. New `pais config init / show / path` commands. New `--config` and `--profile` global CLI flags.
- **Cleanup commands** with `--strategy {auto, api, recreate}` and `--yes` confirmation gating:
  - `pais kb delete <kb> --yes` (existing command, gained confirmation prompt)
  - `pais kb purge <kb>` — delete docs in every index, keep KB
  - `pais index purge <kb> <index>` — delete docs in one index
  - `pais index delete <kb> <index>` — delete the index entirely
  - `pais index cancel <kb> <index>` — stop a running indexing job
- **`pais-dev ingest-suites --replace`** — only re-uploads suites whose origin_name slug matches the input directory; untouched suites stay.
- SDK: `IndexesResource.{delete_document, purge, cancel_indexing}`, `KnowledgeBasesResource.purge`, `dev.ingest.ingest_directory(..., replace=True)`.
- Mock backend: `DELETE /documents/{id}`, `DELETE /active-indexing`, `Store.disabled_endpoints` test hook for exercising probe-then-fallback paths.

### Changed
- `Settings` precedence is now: CLI kwargs → `PAIS_*` env → config-file profile → `.env` → defaults.
- Confirmation prompts on destructive ops; refuse to run in non-TTY without `--yes`.

### Verified PAIS API constraints (drove the design)
- No documented cancel/stop indexing endpoint; no documented per-document DELETE; no batch DELETE. All cleanup ops therefore use a probe-then-fallback pattern (try the obvious REST verb; on 404/405 fall back to delete-and-recreate the index — which changes its `id`, surfaced in CLI output and structured logs).

## 0.2.0 · test-suite ingestion pipeline

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
