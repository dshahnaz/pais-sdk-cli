# Changelog

## 0.6.8 · type-aware shell prompts, model pickers, kb-list resilience

### Fixed
- **Interactive shell typed every option as text.** Pressing "✏ Edit with_counts" (a `bool`) popped a text widget — the user had to literally type `true`. Same for `--output` (only 3 choices), `--workers`, `--chunk-size`, `--epoch`, `--strategy`, `--text-splitting`. Root cause: `from __future__ import annotations` deferred `p.annotation` to the **string** `"bool"`, so `param.annotation is bool` in `_prompts.py` was always False and every branch fell through to plain text. Fix: `_introspect.py` now calls `typing.get_type_hints(cb, include_extras=True)` to resolve PEP 563 strings back to real types before building `ParamSpec`. `_prompts.py` also gained a defensive `_is_type(ann, target)` string-name fallback for any callback whose hints can't resolve. Booleans → `questionary.confirm` (y/N). Literal / Enum / `output` / `strategy` / `text_splitting` → `questionary.select`. `int` / `float` → validated text. `Path` → path widget.
- **`pais kb list --with-counts` crashed when one KB's `/indexes` 422'd.** The landing screen silently suppressed per-KB errors (so "1 indexes" still showed), but `kb_list` had no isolation — one bad KB sank the whole command. Now wrapped in a per-KB try/except: the offending row renders `!` in the `indexes` / `documents` columns and the server's validation detail is echoed on stderr as `warn: kb=<name> …`. Exit code 0 as long as the KB list itself succeeded.
- **`PaisError.__str__` was opaque on 422.** Users saw `codes=[VALIDATION_ERROR]` and had to dig through logs to find which field the server rejected. Now the first `ErrorDetail.loc` + `msg` are appended as `detail=<path>: <message>`. `value` is deliberately excluded — it can carry request payload bits (e.g. a rejected password field).

### Added — UX
- **Model pickers in the shell.** `pais index create` and `pais agent create` no longer ask the user to remember `BAAI/bge-small-en-v1.5` or `openai/gpt-oss-120b-4x` by hand. Both now fetch `GET /compatibility/openai/v1/models`, filter by `model_type` (`EMBEDDINGS` vs `COMPLETIONS`), and present a select list with each model's id + engine suffix. The `setup_agent` workflow's two model FieldSpecs use the same pickers on Edit, and default to the first server-advertised model of the right kind (falling back to the hard-coded defaults only if the list is empty). Every picker keeps the "✏ enter manually" fallback and degrades to free-text on `PaisError` so a flaky `/models` endpoint never blocks the flow.
- `pais.cli._pickers.pick_embeddings_model`, `pick_chat_model`, and the `first_model_id(ctx, kind=…)` helper (used by workflows to pre-seed defaults).
- Registered in `_OVERRIDES`: `("index", "create", "embeddings_model")` → embeddings picker; `("agent", "create", "model")` → chat picker.

### Added — tests (+27, now 351 total)
- `tests/test_prompts.py` (new, 14 tests) — pins widget dispatch over every annotation shape: bool → confirm (including PEP 563 string fallback), Literal + static-enum `output` / `strategy` / `text_splitting` → select, int/float → validated text, Path → path, full-tree audit asserts no option falls through to plain text because of a leaked string annotation.
- `tests/test_errors.py` (new, 5 tests) — enriched `__str__` surfaces `detail=loc: msg`; `value` stays out; `error_from_response(422, …)` wires `detail[]` through.
- `tests/test_cli_kb_list.py` (new, 2 tests) — two KBs, one's `indexes.list` monkeypatched to raise 422; asserts exit code 0, `!` markers in the bad row, and `warn: kb=…` + `detail=query.limit: field required` on stderr.
- `tests/test_interactive_pickers.py` (+6 tests) — model pickers filter by `model_type`, fall back to manual entry on empty list, fall back to text on `PaisError`; `first_model_id` helper returns the first match (or None).

### Internal
- `typing` imported in `_introspect.py` for `get_type_hints` resolution. Failure path preserved — any callback whose hints can't resolve still uses the raw `p.annotation` and relies on the `_is_type` string fallback.

## 0.6.7 · progress bars on long ops, TLS warning dedup, better confirm label

### Added
- **Rich progress bar on `pais kb purge` and `pais index purge`.** A spinner, `N of M` counter, and the current index name (for KB purge) render in place while documents are being deleted. A 250-doc purge now shows `123/250 · index_name · 0:00:04` instead of silent terminal staring. Skipped automatically when `--output` isn't `table` or stdout isn't a TTY — JSON consumers and piped output get no ANSI noise.
- **SDK `on_progress` callback on `Indexes.purge(...)` and `KnowledgeBases.purge(...)`.** Signature: `Callable[[str, ...], None]`. Events emitted:
  - `Indexes.purge`: `collected(total)` → `deleted(deleted, total, doc_id, origin)` × N → `done(deleted, errors, strategy_used)` (plus `error(...)` for non-fallback failures).
  - `KnowledgeBases.purge` wraps each nested call with `index_start(index_id, index_name, i, n)` / `index_done(index_id, deleted)`.
  - Any exception raised by the callback is swallowed — a buggy UI must not corrupt a destructive op (test: `test_purge_on_progress_swallows_callback_exceptions`).
- `src/pais/cli/_purge_progress.py` — reusable Rich `Progress` context manager any future purge-like command can use.
- Tests: `test_purge_on_progress_callback_counts_match`, `test_purge_on_progress_swallows_callback_exceptions`, `test_kb_purge_emits_index_start_and_index_done`, plus 4 in `test_transport_tls_warning.py`.

### Fixed
- **`pais.tls.verification_disabled` WARNING fired per transport construction** — in an interactive session that's ≥ 3 emissions per workflow. Now dedup'd: warned once per `(process, base_url)` via a module-level set in `src/pais/transport/httpx_transport.py`. Different hosts still each get one advisory.
- **Destructive confirm showed `(no args)`** even after the user picked a KB/index via a picker. `_confirmation_label` used to filter by typer's `kind == "argument"` classification, which misses picker-answered options. Now surfaces every answered param (minus the `yes` / `output` / `epoch` presentation flags), so the confirm reads `Really kb purge kb_id='…'?` as expected.

### Migration
- SDK embedders who pass positional args to `Indexes.purge(kb, ix, "auto")` keep working — `on_progress` is an optional keyword-only arg.

## 0.6.6 · `kb prune` paginates, prompts default-selected, quieter by default

### Fixed
- **`pais kb prune` (and `pais kb purge`) stopped after 100 documents.** Root cause: `Indexes.list_documents()` made a single un-paginated `GET /documents` call, which most PAIS servers cap at 100 per page. Indexes with > 100 documents leaked every page beyond the first. Fix: new `Indexes.iter_documents(...)` transparently follows the `has_more` + `last_id` cursor envelope (same pattern the SDK already uses for KBs/indexes/agents via `Resource.list_all`). `purge` now snapshots every matching doc id up-front across all pages, then deletes — robust to cursor invalidation under concurrent delete. Hard cap (1000 pages) guards against a server mis-reporting `has_more=True` forever. 3 new tests in `test_cleanup` seed 250 docs and assert all are removed.

### Changed — UX
- **No more "customize --X?" yes/no gates.** The interactive shell's flat-command dispatcher used to ask *"customize --chunk-size (default: 512)?"* before every optional param. Gone. Instead, a single review-screen shows every optional param with its default pre-filled — press Enter on "Go" to run with all defaults, or pick "Edit X" to change one. Same pattern already powered `setup_agent` / `setup_kb` — now consistent everywhere.
- **Search workflow no longer gates `top_n` / `similarity_cutoff`.** The *"Customize top_n / similarity_cutoff?"* confirm is removed; both fields are always visible in a review screen with defaults (`top_n=5`, `similarity_cutoff=0.0`). Enter = run.
- **Every picker pre-highlights the recommended choice.** `pick_kb` / `pick_index` / `pick_agent` / `pick_mcp_tool` / `pick_splitter_kind` / the `pick_or_create_*` variants all thread `default=` into `questionary.select()`. For pick-or-create flows, the top ★ recent is pre-selected; `pick_splitter_kind` pre-selects `recursive_markdown`. Returning users hit Enter once instead of arrowing down.
- **Review screen says so:** the action picker's instruction now reads `Enter = Go  ·  ↑↓ to pick  ·  Ctrl-C → back` and defaults to "✅ Go (commit)". `next_actions_menu` defaults to the recommended action.

### Changed — Logs
- **Silent by default; detail on `-v` / `-vv`.**
  - no flag  → WARNING (only warnings/errors: TLS-verify-off, purge fallback, retries)
  - `-v`     → INFO (high-signal events: ingest start/done, index recreated)
  - `-vv`    → DEBUG (per-request HTTP traces, latency, status)
  The `--verbose` option is now a count-style flag (`typer.Option(0, "--verbose", "-v", count=True)`). `PAIS_VERBOSE` env respects the tier (`"1"` = INFO, `"2"` = DEBUG).
- **`pais.request` success lines demoted from INFO to DEBUG** in both `httpx_transport.py` and `fake_transport.py`. A ten-KB prune used to dump ~50 `pais.request` lines at INFO; now those appear only at `-vv`. Retries / timeouts / network errors stay at WARNING.
- **Eager `configure_logging` in the Typer root callback** — the verbosity tier applies before the first HTTP request, not only after the first `PaisClient.from_settings` call.

### Added
- `Indexes.list_documents(..., *, limit=None, after=None)` — cursor pagination kwargs.
- `Indexes.iter_documents(..., *, limit=100, max_pages=1000)` — page-walking iterator.
- `pais_mock` GET `/documents` honours `?limit=N&after=<doc_id>`, returns `has_more` + `first_id` + `last_id` for multi-page listings.
- `tests/test_verbosity.py` — locks in the three-tier contract + asserts `pais.request` success stays below INFO.
- `tests/test_workflows_search.py` — regression test that the customize gate is gone.
- `tests/test_cleanup.py` — 3 new pagination tests (seed 250, purge all; iter_documents walks every page; max_pages cap).

### Migration notes
- `Settings().log_level` now defaults to `"WARNING"` in the CLI context (via the root callback). If you embed the SDK, call `configure_logging(level="INFO")` yourself to keep the old floor.
- If you relied on `pais.request` appearing in default stderr, switch to `pais -vv …` or read `~/.pais/logs/pais.log` directly.

## 0.6.5 · relax enum drift, surface logs, ship `pais doctor`

### Fixed
- **`pais models list` crashed on real PAIS** returning `model_engine="LLAMA_CPP"`. The SDK's closed Enum only accepted `VLLM`/`INFINITY`/`OTHER`. Root cause: the published Broadcom doc types every status field as `string` (the enumerated values are examples, not closed sets). Fix: all 11 enum-typed model fields (`model_type`, `model_engine`, `Index.status`, `Indexing.state`, `Document.state`, `data_origin_type`, `IndexRefreshPolicy.policy_type`, `ToolLink.link_type`, `DataSource.type`) → `str`. Enum classes stay defined as named constants for IDE autocomplete (`ModelEngine.VLLM == "VLLM"` still works).

### Added
- **`pais logs path`** — prints the active log file path (for `tail -f $(pais logs path)`).
- **`pais logs tail [-n N] [-f]`** — prints the last N log lines (default 50); `-f` follows (TTY only).
- **`pais logs clear --yes`** — truncates the active log. Rotated backups stay.
- **`pais doctor`** — one-shot diagnostic probe: ping + KB list + indexes + agents + models + mcp_tools. Captures every error with status_code + request_id + redacted response body. Emits a markdown report to stdout AND `~/.pais/logs/doctor-<timestamp>.md` for sharing.
- **Landing screen footer** now shows the log file path + `pais -v for full stream` + `pais logs tail`.
- **`error_banner` footer hint** on every workflow failure: *"Run `pais doctor` for a diagnostic to share."*
- 3 LLAMA_CPP model in mock fixtures so tests cover the doc-string contract.

### Changed
- All status-field enums relaxed to `str` per the doc contract. Enum classes kept as named constants so `isinstance` callers see the change, but equality checks (`m.model_engine == ModelEngine.VLLM`) still work. Flagged in CHANGELOG per the plan.

### Doc-verified facts (added to CLAUDE.md)
- Doc types every status field as `string`. Enum classes are constants only.
- No `/health` endpoint documented. Reachability: HEAD on base URL.
- No server-side log endpoint. Logs are client-side (`~/.pais/logs/pais.log`, rotating). `pais doctor` collates everything.

## 0.6.4 · fix delete + search: SDK reconciled against the published Broadcom doc

Three SDK ↔ doc mismatches found by re-fetching <https://developer.broadcom.com/xapis/vmware-private-ai-service-api/latest/>. The cleanup workflow's "delete looks like it does nothing" trace led to all three.

### Fixed
- **`pais index search` returned 0 hits silently.** SDK posted `{"query": "...", "top_n": 5}` but the doc says `{"text": "...", "top_k": N}`. Server ignored the unknown keys and returned no results. Same wire-format mismatch on the response (doc returns `{"chunks": [...]}`, SDK parsed `{"hits": [...]}`).
- **`pais index delete` silently no-op'd on real PAIS.** Per-index DELETE isn't in the published doc — many deployments 404/405. The SDK assumed 200 = deleted and the cleanup workflow rendered green ✓ regardless. Now raises a new `IndexDeleteUnsupported` exception with actionable alternatives.
- **Cleanup workflow banner lied about success.** A typo on the type-to-confirm prompt printed `[dim]aborted[/dim]` (easy to miss on a long resource name); a successful-looking DELETE that didn't actually remove the row got a green ✓ banner without verification.

### Changed
- **SDK `SearchQuery`** keeps Python field names `query` / `top_n` (callers unchanged) but serializes to the doc-aligned wire body `{text, top_k, similarity_cutoff}` via pydantic `serialization_alias`.
- **SDK `SearchResponse`** now accepts both the doc-aligned `{"chunks": [...]}` shape and the legacy `{"hits": [...]}` shape via a `model_validator(mode="before")`. `.hits` is the canonical Python attribute either way.
- **SDK `SearchHit`** gains `origin_ref` and `media_type` (doc fields); `chunk_id` becomes optional (absent in doc shape; kept for legacy back-compat).
- **`PaisClient.indexes.delete()`** now probes-then-falls-back: 404/405 → `IndexDeleteUnsupported` (with `suggested_alternatives=["Delete the parent KB ...", "Purge contents (--strategy recreate; ...)"]`).
- **Cleanup workflow (Workflow G)** now:
  - Prints a **visible red** *"name didn't match"* line on confirm-by-typing failure (instead of `[dim]aborted[/dim]`).
  - **Verifies deletion** by re-fetching after the DELETE call. Green ✓ banner only if `PaisNotFoundError`; red ✗ if the resource is still listed.
  - Catches `IndexDeleteUnsupported` and presents a 3-option menu: *"Delete the parent KB"* / *"Purge contents (--strategy recreate)"* / *"← back"*.
- **Mock server (`pais_mock`)** updated to emit the doc-aligned wire format for search and delete responses.

### Added
- New SDK exception `pais.errors.IndexDeleteUnsupported` (subclass of `PaisError`).
- New `error_banner` (red) and `partial_banner` (yellow) helpers in `pais.cli._workflows._base` complementing the existing green `done_banner`.
- 15 new tests across `test_search_doc_shape`, `test_index_delete_unsupported`, and extended `test_workflows_cleanup`.

### Doc-verified facts (added to CLAUDE.md)
- Search wire format: request `{text, top_k, similarity_cutoff}`; response `{chunks: [{origin_name, origin_ref, document_id, score, media_type, text}]}`. SDK wraps both with field aliases for back-compat.
- Per-index DELETE is undocumented; some deployments lack it. SDK raises `IndexDeleteUnsupported`; CLI suggests deleting the parent KB instead.

## 0.6.3 · fix: shell logs leaked back through `from_settings` re-configure

Hotfix for v0.6.2. The shell's WARNING override was undone by every `Settings.build_client()` call inside the menu loop — `PaisClient.from_settings(settings)` re-runs `configure_logging(level=settings.log_level)`, and `settings.log_level` was still `"INFO"`. The fix: also mutate `settings.log_level = "WARNING"` (only in the shell, only when `PAIS_VERBOSE` is unset) so subsequent `from_settings` calls keep the WARNING floor.

### Fixed
- Interactive shell no longer leaks `pais.request` / `httpx` INFO lines on every menu refresh — the v0.6.2 quieting now actually sticks across `build_client()` re-configure cycles.

## 0.6.2 · quiet interactive shell + visible back navigation

Purely additive — no breaking changes. Two quality-of-life fixes for the v0.6 interactive shell.

### Changed
- **Interactive shell defaults to WARNING-level logs.** The per-request `pais.request` INFO lines and `httpx`'s `HTTP Request:` lines no longer drown the menu. The TLS-disabled warning still fires (it's important). Non-interactive subcommand calls (`pais kb list`, `pais ingest …`) are unaffected — they still emit INFO-level logs to stderr, so any script that greps them keeps working. The full INFO stream also still rotates to `~/.pais/logs/pais.log` regardless of console verbosity.

### Added
- **`pais -v` / `--verbose` global flag** (and `PAIS_VERBOSE=1` env var) — lifts the shell's WARNING floor back to INFO for troubleshooting. Honoured by every code path via the env var.
- **Visible back navigation in the interactive shell.** Every picker now exposes an explicit `←  back` row alongside `+ create new` and `✏ enter manually`. Every prompt's instruction line shows `Ctrl-C / Esc → back` so the back-shortcut is discoverable. Same `CANCEL` sentinel for both paths — workflows handle them identically.
- **Third-party loggers silenced by default** in `configure_logging`: `httpx` and `httpcore` floor at WARNING; `huggingface_hub` at ERROR. Verbose mode lifts all three.

## 0.6.1 · splitter discoverability + observability

Purely additive — no breaking changes. Picking the right splitter is now obvious without reading source.

### Added
- **Every splitter now declares structured metadata** (`SplitterMeta`): summary, input type, algorithm, chunk-size unit (tokens/chars/file), typical chunk size, token↔char hint, example input, notes. Surfaced everywhere a splitter is shown.
- **`pais splitters list`** default output now has `kind` + `summary`. New `-v/--verbose` adds `input`, `chunk_size`, and `unit` columns.
- **`pais splitters show <kind>`** — replaces the v0.6 raw JSON-schema dump with a rich panel: tagline → input → algorithm → output (unit + typical size + token↔char) → options table (with constraints) → notes. JSON output (`-o json`) returns the meta as a structured dict.
- **NEW `pais splitters preview <kind> <path>`** — runs the splitter against a real file/dir (dry-run, no upload) and reports:
  - chunk count
  - char distribution (min / median / max)
  - **token distribution** (when `tokenizers` is installed) under `BAAI/bge-small-en-v1.5`
  - measured **chars/token ratio** for your actual content
  - first 300 chars of chunk #1 as a sample
  - `--limit N` and `--max-bytes N` caps for directory scans

  Falls back gracefully to char-only stats when `tokenizers` isn't installed.
- **Interactive shell** (`pick_or_create_splitter_config` + workflow B) shows each splitter's summary and typical chunk size inline, then prints a 2-line brief (input + chunk) right before the path prompt.

## 0.6.0 · task-centric workflows + smart landing screen

Purely additive — no breaking changes. The interactive shell now opens with a smart landing screen and offers 7 task-oriented workflows (set up agent, ingest, chat, search, cleanup, …) on top of the v0.5 flat command list.

### Added
- **Smart landing screen.** Bare `pais` (in a TTY) now opens with a one-line state snapshot (KBs · indexes · agents · drift), a recommended workflow based on env state (no agents → "Set up an agent"; drift → "Apply pending TOML"; otherwise → "Chat"), and a compact menu of all 7 workflows + a `📋 all commands…` fallback to the v0.5 flat list. Mode badge is colour-coded (`http` green, `mock` red).
- **7 task-centric workflows** in `pais.cli._workflows.*`:
  - **Set up a chat agent over my docs** — pick-or-create KB → pick-or-create index → optional save to `pais.toml` → create agent (doc-aligned `index_id`) → branch into ingest / chat / status.
  - **Provision KB + index (no agent)** — same first two steps, then stop.
  - **Apply pending TOML config** — drift preview → confirm → run `kb ensure` → branch to ingest each newly-created index.
  - **Ingest data into an index** — pick KB+index → splitter from config-or-prompt → path → optional `--replace` → progress bar → branch to search.
  - **Chat with an agent** — pick agent → multi-line prompt loop with `rich.spinner` while LLM thinks.
  - **Search an index (no LLM)** — pick KB+index → query → ranked hits with score / origin / snippet.
  - **Cleanup (delete KB / index / agent)** — pick kind → pick item → **type-to-confirm** (GitHub-style, the resource name) → delete.
- **Pick-or-create pickers**: `pick_or_create_kb` / `_index` / `_agent` / `_splitter_config` show ★-marked recents at the top, then existing items, then `+ create new` and `✏ enter manually`.
- **Single-screen review** for create flows: instead of 5+ separate yes/no prompts, all defaults are pre-filled in a key-value panel; user picks `✅ Go` / `✏ Edit <field>` / `← back`. Hints (e.g. `chunk_size 512  ↑ ≈ 2KB English text per chunk; tokens, not chars`) explain non-obvious defaults inline.
- **Post-success "what next?" menus** with the most-relevant follow-up highlighted and the rest greyed out (in `mock` mode, "Chat" carries a `(mock — canned answers)` annotation).
- **Type-to-confirm** for destructive ops (the user must type the resource's exact name to proceed). `--quick-confirm` / `-Q` flag and `PAIS_QUICK_CONFIRM=1` env var fall back to `y/N` for power users.
- **Recent-targets memory** at `~/.pais/recent.json` (per-profile, LRU-capped at 10 per kind). Pickers prepend the last-3 with `★`.
- **Safe TOML writeback** in `pais.cli._config_writeback`. Append-only, idempotent, unified-diff preview before write, refuses to write if the existing file fails to parse. Comments and unknown sections above the `# --- added by pais workflows ---` marker stay byte-for-byte. Uses `tomli-w` (new runtime dep, ~30 KB).

### SDK alignment with the official Broadcom doc
> Doc URL added to `CLAUDE.md`: <https://developer.broadcom.com/xapis/vmware-private-ai-service-api/latest/>
- **`AgentCreate` / `Agent` gain `index_id` + `index_top_n`** matching the published spec for `POST /compatibility/openai/v1/agents`. The legacy `tools=[ToolLink]` shape is preserved for back-compat with deployments that need it; new code paths default to `index_id`.
- **`DataOriginType` enum** gains the doc-aligned plural value `DATA_SOURCES` alongside the existing `LOCAL_FILES` and `DATA_SOURCE`.
- New contract test exercises both shapes round-tripping through the SDK + mock.

### Process
- **Standing rule promoted to `CLAUDE.md`**: every plan-mode session that touches PAIS endpoints must `WebFetch` the doc URL **as Step 0** — before any design work. Two past misses (v0.4 `chunk_size` units, v0.6 agent `index_id`) drove this.

## 0.5.0 · interactive shell + `pais-dev` script removed

### ⚠️ Breaking changes
- **`pais-dev` console script removed.** It was a redirect shim since v0.4.0; the entry is now gone from `[project.scripts]`. The `pais.cli.dev` Python module still exists (for stale `python -m pais.cli.dev` callers) and prints the same redirect message. Use `pais ingest <kb_ref>:<index_ref> <path>` instead.

### Added
- **`pais` (no args) drops into an interactive menu** when stdin is a TTY. The menu walks the live typer tree, lists every command with its one-line description, and lets you filter by typing or pick by arrow keys. Drilling into a command prompts for required arguments with type-aware widgets (text / confirm / select / path).
- **Context-aware ref pickers.** When a command needs `kb_ref`, the menu fetches the live KB list from the server and lets you select; same for `index_ref` (scoped to the chosen KB), `agent_id`, splitter `kind`, MCP tools, and cached aliases. Each picker shows alias + name + UUID, includes an "✏ enter manually" fallback, and falls back to a plain text prompt on server errors so the menu never gets stuck.
- **Destructive-op confirms.** For `*_delete`, `*_purge`, `index cancel`, and `agent delete`, the menu shows a single confirm prompt echoing the resolved label (e.g. `Really index delete kb_ref='kb_1' index_ref='idx_1'?`) and auto-passes `--yes` so the underlying command doesn't double-prompt.
- **`pais shell`** — explicit alias for the interactive menu. Forces it on regardless of TTY detection (errors out cleanly if stdin really isn't a TTY).
- **`--no-interactive`** global flag and **`PAIS_NONINTERACTIVE=1`** env var disable the bare-`pais` trigger.

### Safety
- The bare-`pais` interactive trigger is gated on `sys.stdin.isatty()` — `pais | head`, `pais </dev/null`, and CI scripts all keep printing the help banner instead of hanging on input.

## 0.4.2 · `pais status` shows agents + always-on indexes section

Purely additive. No breaking changes.

### Changed
- **`pais status`** now always renders the **Indexes** section (one extra `indexes.list` per KB — same N+1 cost as `kb show`). Empty server → `(none)` placeholder. The `indexes_count` column on the KB table is also always shown. `-c, --with-counts` now only toggles the per-row `documents` aggregate (which is free once the index list is in hand). Behavior matches the user's intent: see indexes by default, see doc totals on demand.

### Added
- **`pais status` Agents section.** Always rendered (with a `(none)` placeholder when the server has no agents). Columns: id, name, model, status. JSON output gains an `agents` key. A flaky agents endpoint is isolated per-section so it never sinks the rest of the status output.

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
