# Changelog

## 0.7.8 · chat-error dumps + `pais doctor` inventory

### Added

- **Chat failures auto-save to `~/.pais/logs/chat-errors/<ts>-<request_id>.json`.** Every exception raised by `client.agents.chat(...)` — in both the `pais chat` REPL and the one-shot `pais agent chat` command — now writes a shareable JSON record: `status_code`, `request_id`, full `PaisError.details` list (codes + `loc` + `msg`), `agent_id`, `profile`, `pais_version`, and a **2000-char excerpt** of the prompt that triggered it (with `prompt_truncated` flag + original `prompt_bytes`). The REPL prints the saved path on screen; the one-shot emits it on stderr. Enough to paste straight into a support ticket without retyping or losing detail. Dir is created with mode `0o700`; atomic `.json.tmp` + `os.replace` write. Non-`PaisError` exceptions include a traceback instead of PAIS-specific fields.
- **`pais doctor` now carries a full environment + inventory snapshot.** The existing probe battery still only counts resources on the probe row, but the new `inventory` block captures the actual records:
  - `knowledge_bases` — `id, name, created_at`
  - `indexes` — `id, kb_id, kb_name, name, embeddings_model, chunk_size, chunk_overlap, status` (flattened across KBs)
  - `agents` — `id, name, model, index_id, index_top_n, session_max_length`
  - `models` — `id, model_type, model_engine`
  - `settings` — **allowlisted** non-secret settings only (`mode, base_url, auth, verify_ssl, timeouts, retry knobs, chat_retry_on_empty, log_level, profile`). Secrets (`password`, `client_secret`, `bearer_token`) are hard-excluded by name; pydantic's `SecretStr` renders as `"**********"` even on the allowlist as belt-and-suspenders.
- The generated `doctor-*.md` report now has collapsible `<details>` sections per inventory bucket + a "Settings (non-secret)" table, so the whole thing stays paste-friendly. `pais doctor --output json` exposes the same data under `inventory` / `settings`.

### Wire-up

- New module `src/pais/cli/_error_dump.py` (~60 LOC) with `dump_chat_error(exc, *, agent_id, prompt, profile=None, dest_dir=None) -> Path`.
- Dump failures are caught and reported as `[dim](could not save error dump: ...)[/dim]` — saving an error dump never hides the original error.
- `DoctorReport` gained two fields (`inventory`, `settings_dump`) serialized through both `to_dict()` and `to_markdown()`.

## 0.7.7 · auto-retry empty chat 200s + `pais.response.chat` diagnostics

### Fixed

- **`pais chat` no longer silently returns empty responses after a cold start.** Root cause (reported in the field): some PAIS deployments accept a chat request before the vLLM worker is fully warm and return `HTTP 200` with an empty `choices[0].message.content` — the existing cold-start retry loop only fires on `502`, so the empty body slipped through and the REPL drew an empty panel (or a script got no text to act on). The transport now also retries on `200` + empty-content on chat paths, reusing the same `chat_cold_start_delay` backoff and the shared `max_attempts` budget. Emits a `pais.request.empty_content_retry` warning so every auto-retry is auditable in `~/.pais/logs/pais.log`.
- Shape detection is conservative: only retries when `choices[0].message.content` is explicitly present and empty/whitespace. Unknown or incomplete bodies (missing `choices`, non-string `content`, etc.) return through unchanged — we don't hammer the server on responses we can't classify.

### Added

- **`pais.response.chat` log line** emitted at INFO level on every chat-path success, carrying `finish_reason`, `content_len`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `path`, `request_id`. Makes empty-response diagnosis recoverable for scripted / non-interactive callers and surfaces cleanly in `pais doctor` / `pais logs tail`. Content text itself is never logged.
- **`PAIS_CHAT_RETRY_ON_EMPTY` (env) / `chat_retry_on_empty` (config)** — opt out of the empty-content retry when you need raw behavior (e.g. debugging a content-filter refusal that legitimately returns empty text). Default `true`.
- *(Consolidates unreleased v0.7.6)* **Empty-response diagnostics in the REPL.** `pais chat` now prints `finish_reason=... tokens in=X out=Y total=Z` in dim below every turn, and switches to a yellow warning panel with likely-cause hints (context window, content filter, zero-token truncation) when content is empty. `/file`'s large-file threshold dropped 1 MB → 50 KB (~12 K tokens) with a context-window-specific warning.

### Changed

- The chat-path retry budget is shared between the existing 502 cold-start branch and the new empty-content branch — total chat attempts still bounded at `max(retry_max_attempts, chat_cold_start_retries)` (default 3). Operators tuning `retry_max_attempts` should size accordingly.

## 0.7.6 · surface empty-response diagnostics in `pais chat`

### Fixed

- **Empty chat responses no longer render as an empty green panel.** Report from the field (v0.7.5 user loaded a 76 KB prompt via `/file` and got back an empty box with no clue why): the server returned HTTP 200 with `choices[0].message.content = ""`, but the REPL printed it verbatim without showing `finish_reason` or token usage. The chat panel now detects an empty body, switches to a yellow border, and prints `finish_reason=...  tokens in=X out=Y total=Z` plus a one-line hint listing the common causes (context-window overflow → `length`, content filter → `content_filter`, zero-token truncation). Non-empty responses print the same `finish_reason` + usage footer in dim below the panel so users get the signal on every turn.
- **`/file` large-file warning threshold lowered from 1 MB to 50 KB** (~12 K tokens at 4 chars/token). The warning message now explicitly mentions context-window risk and the possibility of an empty response, instead of just echoing the file size.

## 0.7.5 · load a file as the chat prompt

### Added

- **Load a file's contents as the user message in `pais chat`.** Long prompts (playbooks, specs, test-suite markdown) are awkward to paste into the multiline input — pasted newlines interact badly with the `⏎⏎ to send` convention and the input is hard to iterate on. Two entry points now accept a path:
  - Interactive REPL: type `/file <path>` at the `you:` prompt. The file is read as UTF-8 and sent as the turn's message. On read error the loop prints a red `error:` line and re-prompts rather than exiting. A `[dim]loaded N bytes from <name>[/dim]` line confirms the send; files over 1 MB emit a yellow warning but still go through.
  - One-shot: `pais agent chat <agent_id> --file prompt.md` (short form `-F`, since `-f` is `--force`). The positional `MESSAGE` argument is now optional — exactly one of `MESSAGE` or `--file` must be supplied.
- Wire shape is unchanged: `ChatMessage.content` is still a `str`; the feature only changes how the CLI produces that string.

## 0.7.4 · `agent create` 500 on empty tools; session-length CLI flags

### Fixed

- **`pais agent create` no longer 500s against real PAIS when `--index-id` is used.** Root cause: `AgentCreate.tools` defaulted to `[]` and the SDK serializes with `model_dump(exclude_none=True)`, which drops `None` but not empty containers — so an empty `tools: []` always rode onto the wire alongside `index_id`, and some PAIS deployments return HTTP 500 on that combination (a minimal `curl` with the same name/model/index_id succeeds 201). `tools` is now `list[ToolLink] | None = None`, so it stays off the wire unless the caller explicitly populates it (legacy MCP path via `--kb-search-tool` is unchanged). Same treatment applied to `session_max_length`, `session_summarization_strategy`, `index_reference_format`, `chat_system_instruction_mode`, and `completion_role`: defaults now `None` so the server owns the defaults instead of the SDK hard-coding `10000` / `"delete_oldest"` / `"structured"` / `"system-message"` / `"assistant"`. A new `tests/test_agent_create_wire_shape.py` pins the exact JSON body shape via a spy transport.

### Added

- **`pais agent create --session-max-length` and `--session-summarization-strategy`** — tune the agent's in-session history window and trim strategy from the CLI. Omit to use the server's default (previously the SDK silently forced 10000 / `"delete_oldest"`). The options also show up in the interactive shell's optional-review screen.

## 0.7.3 · `agent create` kb_ref leak; log shell exceptions to file

### Fixed

- **`pais agent create` no longer crashes with `TypeError: agent_create() got an unexpected keyword argument 'kb_ref'`.** Regression from v0.7.2: the KB→index picker cascade stashes the picked KB into `PickerContext.answers["kb_ref"]` as scratch state, but the interactive dispatcher then fed the full `answers` dict through `spec.callback(**answers)` — and `agent_create` declares no `kb_ref` parameter. The dispatcher now filters `answers` to the set of declared `spec.params` before invoking, so any picker's scratch keys stay contained. Defense-in-depth — future pickers that stash side-channel state won't regress the same way.
- **Interactive shell failures now land in `~/.pais/logs/pais.log`.** Previously the top-level exception handlers in `enter_interactive` only printed `error: …` to the console; the traceback never reached the rotating file handler, so `pais doctor` / `pais logs tail` couldn't recover it for support. Both the flat-menu dispatch path and the workflow path now emit `shell.command_crashed` / `shell.workflow_crashed` (with full traceback) via the existing structlog logger before printing.

## 0.7.2 · `agent create` KB→index picker cascade; hidden-param OptionInfo leak

### Fixed

- **Interactive `pais agent create` now shows a live KB picker followed by an index picker, instead of a raw text prompt.** Previously, because `agent create` has no `kb_ref` parameter, `pick_index` tripped its "kb_ref not yet chosen" fallback and dropped the user into `? type the index alias or UUID:`. The picker now cascades — when no KB is in scope it calls `pick_kb` first, stashes the pick in the shared `PickerContext.answers`, and then lists indexes under it. `pick_or_create_index` does the same against `pick_or_create_kb` and propagates `← back` / `+ create new` cleanly.
- The fix is applied *inside* the pickers, so any future command that binds an index picker without a preceding KB parameter (e.g. a hypothetical `agent update --index-id`) inherits the cascade automatically.
- **Hidden params no longer leak `typer.OptionInfo` into callbacks.** The interactive dispatcher skipped `hidden=True` params entirely, so they weren't added to the `answers` dict — and `spec.callback(**answers)` fell back to the function's declared default, which is the raw `typer.Option(None, …)` `OptionInfo` wrapper. In `agent create`, the legacy `--kb-search-tool` (hidden) tripped `if kb_search_tool:` (always truthy on an `OptionInfo`) and crashed with `ToolLink.tool_id: Input should be a valid string [input_type=OptionInfo]`. The dispatcher now injects the hidden param's declared default (here `None`) into `answers` before calling the callback — matching the pattern already used for destructive `yes`.

## 0.7.1 · `agent create` doc-aligned; survive undocumented MCP endpoint

### Fixed

- **Interactive `pais agent create` no longer crashes on deployments where `/control/mcp-servers/tools` returns an unexpected shape.** The picker dispatch for the `agent create` menu used to route through `pick_mcp_tool`, which hit an undocumented endpoint; on production PAIS the response didn't match our strict `McpTool` model and blew up with `ListResponse[McpTool]` validation errors (`name` missing, `server` wrong type, `input_schema` wrong type). The shell now prompts for an index instead — aligning with the published doc shape — and the MCP tools resource degrades to an empty list + one warning when the server returns an unexpected payload, rather than raising.

### Added

- **`index_similarity_cutoff` on `Agent` / `AgentCreate` / `AgentUpdate`** — doc-aligned optional field that travels alongside `index_id` and `index_top_n`. Defaults to `None`, so older PAIS builds that ignore unknown fields still round-trip cleanly.
- **`pais agent create --index-id / --index-top-n / --index-similarity-cutoff`** — new doc-aligned flags on the flat CLI command. When `--index-id` is set, the three fields are sent on `AgentCreate` directly (no `tools` array). The `/setup-agent` workflow's review screen also prompts for the similarity cutoff now.
- **`typer.Option(..., hidden=True)` is honored by the interactive shell.** `_introspect.ParamSpec` gained a `hidden` field; `interactive.py` skips hidden params in the prompt loop. Keeps scripted back-compat flags callable without re-exposing them in the menu.

### Changed

- **`pais agent create` interactive flow now prompts for an index, not an MCP tool.** The picker dispatch swapped `("agent","create"), "kb_search_tool" → pick_mcp_tool` for `("agent","create"), "index_id" → pick_index`.
- **`--kb-search-tool` is now hidden on `pais agent create`** (still callable from scripts for deployments that wire agents via legacy `ToolLink`s, but no longer surfaces in `--help` or the interactive prompt).
- **`McpTool.name` and `McpTool.server` are now optional.** The endpoint is undocumented (CLAUDE.md constraint #8) and its shape varies across deployments; only `id` is required. Callers that need a label should use `t.name or "?"` / `t.server or "built-in"`.

## 0.7.0 · dedicated test-suite splitters, preview --dump, scaffolder

### ⚠️ Breaking changes

- **Removed four generic splitters.** `passthrough`, `text_chunks`, `markdown_headings`, `test_suite_md` are gone from `src/pais/ingest/splitters/`. The replacement surface is two purpose-built splitters: `test_suite_bge` (tuned for `BAAI/bge-small-en-v1.5`, 400-token budget, suggested index `chunk_size=512`) and `test_suite_arctic` (tuned for `Snowflake/snowflake-arctic-embed-m-v2.0`, 1500-token budget, suggested index `chunk_size=2048`). Existing `pais.toml` files referencing a removed kind will fail at parse time with a clear error listing the new registered kinds.
- **Removed legacy helpers.** `pais.dev.split_suite`, `pais.dev.markdown`, `pais.dev.ingest` deleted — their only consumers were the removed splitters. `pais.cli.dev` redirect shim also deleted (`pais-dev` console script was removed in v0.5.0; the shim was a soft-landing for stale installs).

### Migration

If your TOML references one of the removed splitters:

```diff
 [profiles.default.knowledge_bases.test_suites.indexes.splitter]
-kind = "test_suite_md"
+kind = "test_suite_bge"
```

Pick `test_suite_bge` if your index's `embeddings_model_endpoint` is `BAAI/bge-small-en-v1.5` (the common case), `test_suite_arctic` if it's `Snowflake/snowflake-arctic-embed-m-v2.0`.

If you need a different splitter shape (raw passthrough, generic markdown, etc.), run:

```bash
pais splitters new my_custom          # interactive scaffolder
```

which generates the splitter file + test stub + `__init__.py` registration + doc row. Ships the contract by construction.

### Added — splitters

- **`test_suite_bge` / `test_suite_arctic`** (`src/pais/ingest/splitters/test_suite_{bge,arctic}.py`). Both emit one per-suite overview chunk + one per test case, each prepended with a compact 2-line breadcrumb (`# Suite: X | Testbed: Y | Components: A, B`). The breadcrumb lives **inside the chunk body** so the embedding vector captures suite-level context even when the chunk is retrieved alone — this is what prevents the "naked `**Key Operations**:` fragment" RAG failure.
- **Shared core** (`src/pais/ingest/splitters/_test_suite_core.py`): markdown parser (fence-aware), testbed/components extractors, `fit_to_budget` ladder (whole-case → sub-sections at `**Label**:` → lines → single line), `emit_chunks` orchestrator. Both splitters are ~100 lines each.
- **Optional Anthropic contextual retrieval** via `with_context_llm = true` / `--with-context-llm`. Adds an LLM-generated one-sentence context per chunk with prompt caching on the document block (49 % recall gain per [Anthropic 2024](https://www.anthropic.com/news/contextual-retrieval); ~$1-3 for 300 suites with Haiku 4.5). Install `pip install 'pais-sdk-cli[contextual]'` and set `ANTHROPIC_API_KEY`.

### Added — SplitterMeta

`src/pais/ingest/splitters/_base.py::SplitterMeta` gained three optional fields:

- `target_embeddings_model: str | None` — e.g. `"BAAI/bge-small-en-v1.5"`
- `suggested_index_chunk_size: int | None` — tokens
- `suggested_index_chunk_overlap: int | None` — tokens

Rendered in `pais splitters show <kind>` and `pais splitters preview ...` as a **Recommended index config for this splitter** footer, so users know exactly what `IndexCreate` body to pass.

### Added — preview `--dump` + `--show-all`

`pais splitters preview <kind> <path> --dump <dir>/` writes every emitted chunk to disk (filename = `origin_name`) so you can open each one and verify size/breadcrumb/semantic slicing before committing to an upload. `--show-all` prints each chunk's header + first 200 chars inline. Both work with table and JSON output modes.

### Added — scaffolder (`pais splitters new <kind>`)

Interactive prompts collect the meta fields; generates `src/pais/ingest/splitters/<kind>.py`, `tests/test_splitter_<kind>.py`, patches the `__init__.py` import list (alphabetized), and appends a row to `docs/ingestion.md` (via the `<!-- splitters-table-end -->` marker). `--dry-run` prints what would be written; `--yes` skips overwrite prompts.

### Added — pre-flight check in `pais ingest`

Before starting the ingest, compares the splitter's declared `target_embeddings_model` / `suggested_index_chunk_size` against the index's actual config. Warns (non-blocking) on mismatch — catches the `test_suite_bge` → arctic-embed index footgun before retrieval quality tells you.

### Added — tests (+14 new; -28 removed; now 363 total)

- `tests/test_splitter_test_suite_core.py` (new, 15 tests) — unit tests for the shared parse/extract/fit/emit helpers.
- `tests/test_splitter_test_suite_bge.py` (new, 8 tests) — end-to-end on `tests/fixtures/test_suites/Access-Management.md`: every chunk ≤ 400 tokens, breadcrumb present, filename convention, group_key, metadata.
- `tests/test_splitter_test_suite_arctic.py` (new, 5 tests) — arctic target + 1500-tok budget + output parity with bge for the sample fixture.
- `tests/test_splitters_new_cmd.py` (new, 9 tests) — scaffolder's render/update/overwrite/dry-run paths.
- `tests/test_splitter_preview.py` (rewritten, 11 tests) — now covers `--dump` (every chunk on disk), `--show-all` (first_chars populated), and the suggested-index footer in table + JSON output.
- `tests/test_splitter_registry.py` (rewritten, 5 tests) — asserts exactly `{test_suite_bge, test_suite_arctic}` are registered and removed kinds raise KeyError (so stale TOML fails loudly).
- Deleted: `tests/test_split_suite.py`, `tests/test_ingest.py`, `tests/test_markdown_parser.py`, `tests/test_cli_dev.py` — tested removed modules.
- Updated: `tests/test_ingest_runner.py` (local `_FakeSplitter` instead of `passthrough`), `tests/test_kb_ensure.py` / `tests/test_config_writeback.py` / `tests/test_status_cmd.py` / `tests/test_interactive_pickers.py` — swapped removed kinds for the new ones.

### Added — optional extra

`[project.optional-dependencies] contextual = ["anthropic>=0.40"]`. Base install stays lean (no Anthropic SDK by default).

### Test fixture

`tests/fixtures/test_suites/Access-Management.md` — 256-line canonical test-suite file committed to the repo as the golden-path fixture.

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
