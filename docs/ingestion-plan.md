# Test-Suite Ingestion Pipeline for PAIS KB

## Context

User has ~300 markdown test-suite files (shape: `~/Downloads/Access-Management.md`) that will feed a single PAIS knowledge-base index. Each file is highly structured markdown: `# Suite` → `## Overview / Deployment / Components`, `## Test Coverage` with many `### testName` sections, `## Technology Stack`. An agent on top of this KB must answer questions like "what does `testEditUserRole` validate?", "which suite tests role creation?", "which tests depend on X?".

The SDK foundation is already built and pushed to `github.com/dshahnaz/pais-sdk-cli` (prior plan). This plan adds the **ingestion pipeline** on top.

## Verified facts from PAIS API docs (not assumptions)

1. **`chunk_size` is measured in TOKENS.** Docs example: `"chunk_size": 100` with narrative "text chunks of 100 tokens in size". User's cap of **512 means 512 tokens ≈ ~2000 characters** (bge-small tokenizer). This reverses my earlier mistake (I was treating 512 as characters).
2. **`chunk_overlap` is also in tokens.**
3. **`text_splitting: "SENTENCE"`** is the only documented splitter; PAIS splits at sentence boundaries when a document's tokenized length exceeds `chunk_size`.
4. **No metadata / tag / attribute field exists on documents or chunks.** The only durable label is `origin_name` (the uploaded filename). Confirmed by cross-reading the official docs and the user's `pais_agent_interactive.py` reference script — the upload endpoint accepts only `file` (multipart).
5. **No metadata-filtered search.** Search returns `{origin_name, origin_ref, document_id, score, media_type, text}` per hit. Filtering "only in suite X" can only happen in the agent prompt or by post-filtering `origin_name` client-side.

Consequences for design:
- Any suite/section label we want the agent to see must live **in the file's text** (for the agent reading the chunk) **and in the filename** (for unambiguous citation).
- Pre-splitting into one-file-per-atomic-section gives us deterministic chunk boundaries and guaranteed labeling on every chunk.

## Design decisions (locked with user)

- **Atomic unit = one logical section.** For `Access-Management.md` that's 1 overview + N test cases + 1 tech-stack = ~12 files. Across 300 suites: ~3,600 files total. Well within PAIS's scale.
- **No separate "suite summary" doc.** User rejected it ("chunk size already very small"). Justification: with pre-split per-section files and the breadcrumb header in each, the suite name appears in every single chunk's text — the agent can already answer "which suite tests X?" by grepping origin_name or reading the header.
- **`chunk_size = 512 tokens`**, honoring user's stated cap.
- **`chunk_overlap = 64 tokens`** — defensive, not load-bearing. With pre-splitting, each emitted file is sized to fit in one PAIS chunk, so overlap is almost never triggered. 64 tokens protects against tokenizer variance on borderline sections.
- **Per-file hard cap = 400 tokens** (leaves 112-token / 22% headroom under the 512 index cap). Measured with the exact tokenizer the index uses — `BAAI/bge-small-en-v1.5` loaded via HuggingFace `tokenizers` library. No character-count approximations. Sections exceeding 400 tokens are sub-split at paragraph boundaries first, then sentence boundaries if needed, into `…__part1.md`, `…__part2.md`, each re-prefixed with the breadcrumb and re-measured.
- **Token budget breakdown** per emitted file:
  - Breadcrumb header (fixed 3 lines): ~18 tokens
  - Section body: ≤ 382 tokens
  - Total: ≤ 400 tokens, guaranteeing one file = one PAIS chunk.
- **New dependency:** `tokenizers>=0.15` (HuggingFace, pure-Rust, ~7 MB). Added as **optional** under `[project.optional-dependencies] dev` (also needed at ingest time, so included there). First use downloads the bge-small vocab to `~/.cache/huggingface/` then works offline. Splitter raises a clear error if the library is missing.
- **`text_splitting = "SENTENCE"`** — the documented default, acts as a safety net if an emitted file somehow exceeds the token cap.
- **Breadcrumb header written into every emitted file body:**
  ```markdown
  # Suite: <SuiteName>
  ## Section: <SectionName>
  ## Kind: overview | test | tech_stack

  <original section body>
  ```
  For any section sub-split into parts, every part carries the same header. This means every chunk PAIS ever returns will contain the suite name and section name in its text, regardless of which split it is.
- **Filename convention** (becomes `origin_name` in PAIS):
  ```
  <SuiteName>__<order>_<kind>__<SectionSlug>.md
  e.g.  Access-Management__10_test__testEditUserRole.md
        Access-Management__05_overview.md
        Access-Management__20_tech_stack.md
  ```
  Order digits: `05=overview`, `10=test`, `20=tech_stack`. Lexicographic sort matches reading order.
- **Fixture stays local-only** (user choice). Tests that need `Access-Management.md` read it from `~/Downloads/` and **skip with a clear message** when absent. CI never depends on it; instead CI uses a small synthetic markdown fixture embedded in the test file.

## How "one section split across chunks" works (answers user's question)

**Question (user):** if a single test case spans 2 chunks, will search still find it?

**Answer:** Yes, because:

1. **Pre-splitting usually prevents it.** If `testEditUserRole` is 300 tokens, it's one file → one chunk. Done.
2. **If a section truly exceeds 512 tokens**, the splitter pre-emits it as `…__part1.md` + `…__part2.md`. Each part is a separate *file* with its own breadcrumb — each becomes its own chunk in PAIS. Search scores them independently.
3. **If PAIS ever re-splits a file** (tokenizer surprise, e.g. a section we estimated at 400 tokens that's actually 530), SENTENCE splitting cuts at a sentence boundary. With 64-token overlap, both chunks share 1–2 sentences near the boundary. Queries matching anywhere in the section match at least one chunk, often both.
4. **The agent's `index_top_n = 5`** — so even in the worst case, both halves of a split section are retrieved together.
5. **The breadcrumb in every chunk** means the agent always knows *which* suite and *which* test case it's looking at, even if it only received the tail half.

Net: one section being represented by 2 chunks instead of 1 slightly dilutes ranking but never loses retrieval.

## Pipeline / flow — end to end

```
┌────────────────┐   ┌───────────────┐   ┌─────────────────┐   ┌──────────┐
│  300 .md files │──▶│  splitter     │──▶│ ~3,600 section  │──▶│  PAIS    │
│  one per suite │   │  (pais.dev.   │   │ files           │   │  KB      │
│                │   │   split_suite)│   │ (with header +  │   │  + index │
└────────────────┘   └───────────────┘   │ origin_name)    │   │          │
                                         └─────────────────┘   └──────────┘
                                                  │                  ▲
                                                  ▼                  │
                                         ┌─────────────────┐         │
                                         │  batch uploader │─────────┘
                                         │  (pais-dev      │
                                         │   ingest-suites)│
                                         └─────────────────┘
```

Concrete CLI flow the user will run:

```bash
# 0. one-time: create KB + index (existing CLI, unchanged)
pais kb create --name test-suites --output json
# → kb_abc

pais index create kb_abc \
    --name ts-idx \
    --embeddings-model BAAI/bge-small-en-v1.5 \
    --chunk-size 512 \
    --chunk-overlap 64 \
    --output json
# → idx_xyz

# 1. dry-run split one file to inspect output (no upload)
pais-dev split-suite ~/suites/Access-Management.md --out ./out/
ls ./out/Access-Management__*.md    # inspect

# 2. split + upload a single suite (for iteration)
pais-dev ingest-suite ~/suites/Access-Management.md \
    --kb kb_abc --index idx_xyz

# 3. bulk ingest all 300
pais-dev ingest-suites ~/suites/ \
    --kb kb_abc --index idx_xyz \
    --workers 4 \
    --report ./ingest-report.json
# shows a rich progress bar, logs every upload, writes a JSON report

# 4. wait for indexing to finish (existing CLI)
pais index wait kb_abc idx_xyz
```

## Token validation — three enforcement layers

1. **Splitter-internal check.** `src/pais/dev/token_budget.py` loads the bge-small tokenizer once and exposes `token_count(text: str) -> int`. `split_suite()` calls it on every emitted `rendered` string. If any result > 400 tokens, the function sub-splits and re-measures. An emitted file over 400 tokens is an internal error, not a warning.
2. **Unit test on every emitted file.** `tests/test_split_suite.py` asserts `token_count(section.rendered) <= 400` for every section produced from synthetic fixtures. CI-blocking.
3. **Ingest-time re-check.** `pais-dev ingest-suites` re-measures each file before POSTing to PAIS. Over-budget files fail loudly with suite+section identifiers rather than silently letting PAIS re-split in opaque ways. Provides a distribution summary in the report: `{min, p50, p95, max}` tokens across all ingested sections.

Optional fourth layer (when `~/Downloads/Access-Management.md` is present locally): an opt-in test measures the real file's section distribution and prints histograms — used to catch tokenizer surprises early on real data.

## Files to create

All in the existing repo `pais-sdk-cli/`:

- `src/pais/dev/__init__.py` — new package for developer-time helpers
- `src/pais/dev/markdown.py` — markdown section parser (heading-based, no external deps)
- `src/pais/dev/token_budget.py` — bge-small tokenizer wrapper + `token_count()` + `BUDGET=400` constant
- `src/pais/dev/split_suite.py` — `split_suite(path) -> list[SplitSection]` core API, enforces the budget
- `src/pais/dev/ingest.py` — `ingest_file(...)` and `ingest_directory(...)` using `PaisClient`
- `src/pais/cli/dev.py` — `pais-dev` CLI group with `split-suite`, `ingest-suite`, `ingest-suites`
- **Wire `pais-dev` entrypoint** in `pyproject.toml` `[project.scripts]` → `pais-dev = "pais.cli.dev:main"`
- Tests:
  - `tests/test_markdown_parser.py` — parsing edge cases (nested headings, empty sections, trailing whitespace)
  - `tests/test_split_suite.py` — golden tests using a small **synthetic** fixture embedded in the test + `pytestmark = pytest.mark.skipif(...)` block that runs the real `Access-Management.md` path when `~/Downloads/Access-Management.md` exists
  - `tests/test_ingest.py` — end-to-end: split synthetic fixture, upload to the fake-transport + Store, query, assert hits contain the right breadcrumb

## Existing code to reuse (no duplication)

- `PaisClient.indexes.upload_document(kb_id, index_id, file_path)` — already implemented in `src/pais/resources/indexes.py:69`. The ingester just loops over split files and calls this.
- `PaisClient.indexes.wait_for_indexing(...)` — `src/pais/resources/indexes.py:107`. The batch command optionally calls this at the end.
- `pais.logging.get_logger` + `new_request_id` — each ingest run gets a single request_id so all upload logs can be correlated on the user's PAIS side.
- `rich.progress.Progress` — already a dep; used for the batch progress bar.
- `Store` + `FakeTransport` — already tested; reused in `test_ingest.py` so no real PAIS needed for CI.

## Splitter internals (concrete)

`split_suite(md_path) -> list[SplitSection]` where:

```python
@dataclass
class SplitSection:
    suite_name: str       # "Access-Management"
    section_name: str     # "testEditUserRole" or "overview" or "tech_stack"
    kind: Literal["overview", "test", "tech_stack"]
    order: int            # 5 / 10 / 20
    part: int | None      # None for single-chunk sections, 1/2/… for sub-splits
    body: str             # original section body (no header)
    filename: str         # "Access-Management__10_test__testEditUserRole.md"
    rendered: str         # full file content: breadcrumb header + body
```

Algorithm:
1. Read file, parse top-level `# H1` to get suite name.
2. Walk lines, tracking current H2/H3 heading. Group:
   - Everything under `## Overview`, `## Deployment Information`, `## Components` → one atom `overview`
   - Each `### Test` inside `## Test Coverage` → one atom `test`
   - `## Technology Stack` → one atom `tech_stack`
   - Ignore `## Test Coverage` as a standalone heading (it's a container)
3. For each atom: prepend breadcrumb header, then call `token_count(rendered)`. If `> 400` tokens: recursively sub-split at paragraph boundaries (blank-line) into `part=1, part=2, …`, each re-prefixed with the breadcrumb and re-measured. If paragraphs alone can't fit, sub-split at sentence boundaries. An emitted atom > 400 tokens after all sub-splitting attempts raises `SectionTooLarge` (would indicate malformed input — a monster single sentence).
4. **Section-name slugification**: the part of the filename after `__<order>_<kind>__` uses only `[A-Za-z0-9_-]`. Any other characters in the source H3 are replaced with `_`. Empty section names fall back to `section_<order>`.
5. Return list of `SplitSection`.

`write_sections(sections, out_dir)` writes files. `upload_sections(client, kb, index, sections)` uploads them via the existing SDK.

## Batch ingester behavior

- Walks input directory recursively for `*.md`.
- For each file: split → upload each section → record success/failure.
- Uploads are parallelized across `--workers N` threads (default **4** — conservative enough not to overwhelm a single-node internal PAIS; tuneable up to 16 for larger deployments).
- Writes `./ingest-report.json`: `{suite_name, file, sections_emitted, sections_uploaded, errors: [...]}` per file, plus a global footer with token-count distribution across all emitted sections (`{min, p50, p95, max}`).
- On any 5xx during upload: retries via the SDK's transport layer (already implemented). Persistent failures are recorded in the report and the run continues.
- Final summary: `✔ 298 suites / 3,412 sections uploaded · ✗ 2 suites failed · see report.`
- **Idempotency**: a re-run uploads the same `origin_name`s again — PAIS will store duplicates with new `document_id`s. First iteration does **not** implement dedupe. Users who need a clean re-ingest either (a) delete the KB and recreate, or (b) wait for Phase 2's `--replace` flag (listed in Out-of-scope). This limitation is explicitly called out in the README runbook.
- **Content hygiene note in README**: body content is uploaded to PAIS as-is. If suite files contain internal hostnames / IPs / credentials, the user is responsible for scrubbing before ingesting. The structured logger redacts secret-looking *keys* but cannot scrub arbitrary prose.

## Tests

All tests use the existing fake-transport + `Store` — no real PAIS needed. The synthetic fixture used across tests is **shape-matched to `Access-Management.md`**: same H1/H2/H3 hierarchy, same section kinds, so tests accurately model real input.

1. **`test_markdown_parser.py`**
   - Parses a synthetic `# Suite\n## Overview\n...\n### test1\n...\n### test2\n...` fixture
   - Asserts: correct H1 extraction, correct section grouping, correct ordering, empty-section handling, heading-in-code-fence is ignored, trailing-whitespace tolerance
   - Slug sanitization: section name `"test with spaces & punct!"` → filename contains `test_with_spaces___punct_`
2. **`test_token_budget.py`** (new)
   - Round-trip: `token_count("hello world")` returns a stable integer
   - Fails with a helpful `ImportError`-wrapped message mentioning `pip install pais-sdk-cli[dev]` when `tokenizers` is not installed
3. **`test_split_suite.py`**
   - Synthetic fixture → asserts 3 expected sections emitted with exact filenames + breadcrumb headers
   - **Token budget assertion**: every emitted section satisfies `token_count(section.rendered) <= 400` using the bge-small tokenizer
   - Oversized synthetic section → asserts sub-split into `part1` + `part2`, each ≤ 400 tokens, each with breadcrumb, each re-prefixed
   - Pathological input: a single ~2,000-token paragraph raises `SectionTooLarge` (not silent truncation)
   - **Optional real fixture**: when `~/Downloads/Access-Management.md` is present, asserts 12+ sections emitted, every filename starts with `Access-Management__`, every section ≤ 400 tokens, and prints the token-count distribution (min/p50/p95/max) for visibility
4. **`test_ingest.py`**
   - Splits synthetic fixture, uploads all sections to in-process `Store` via `FakeTransport`
   - Creates an agent with a KB-search tool bound to the index
   - Asks the agent "what does testX validate?" and asserts the hit's text contains `Section: testX`
   - Exercises `ingest-directory` against a tmp dir of 3 synthetic suites
   - Asserts `ingest-report.json` is written with the expected keys + token-distribution footer
   - Simulated 502 on one upload → asserts retry + final success; simulated persistent 500 → asserts error recorded in report but run completes for other files
5. **`test_cli_dev.py`** (new)
   - `pais-dev split-suite` + `pais-dev ingest-suite` + `pais-dev ingest-suites` via `typer.testing.CliRunner`
   - Asserts exit codes (0 / 1 / 2 / 3) consistent with main CLI's scheme
   - Asserts `--output json` produces valid JSON

**Coverage target**: ≥ 85% on `src/pais/dev/` (same bar as the rest of the SDK).

## Verification (end-to-end)

```bash
# static checks
uv run ruff check && uv run ruff format --check && uv run mypy src

# full test suite (existing + new)
uv run pytest -q

# manual smoke against the local mock server
uv run python -m pais_mock --port 8080 &
export PAIS_MODE=http PAIS_BASE_URL=http://127.0.0.1:8080/api/v1 PAIS_AUTH=none

pais kb create --name ts --output json          # → kb_id
pais index create <kb_id> --name ix \
    --embeddings-model BAAI/bge-small-en-v1.5 \
    --chunk-size 512 --chunk-overlap 64 --output json    # → ix_id

mkdir -p /tmp/suites && cp ~/Downloads/Access-Management.md /tmp/suites/
pais-dev ingest-suites /tmp/suites/ --kb <kb_id> --index <ix_id>
pais index search <kb_id> <ix_id> "what does testEditUserRole validate?"
# expect a hit with origin_name Access-Management__10_test__testEditUserRole.md
```

## Documentation updates (explicit, not implicit)

Every user-facing change needs a doc change. This iteration updates:

1. **`README.md`** — add a top-level **"Ingest test suites"** section after the "Three runbooks" section. Content:
   - One-paragraph problem statement (300 suites → searchable KB)
   - The 4-command runbook from the "Pipeline / flow" section above (copy-pastable)
   - Pointer to `docs/ingestion.md` for the full design
   - Installation note: `uv sync --extra dev` for the `tokenizers` library
   - Content-hygiene warning (do not ingest unredacted internal data into a shared PAIS)
   - Idempotency note (re-runs create duplicates until Phase 2)
2. **`CONTRIBUTING.md`** — add:
   - `tokenizers` is an optional dev dep; first run downloads ~10 MB vocab to `~/.cache/huggingface/`
   - `pais-dev` CLI exists alongside `pais` CLI; list commands
   - Running the tokenizer-dependent tests requires the dev extras
3. **`docs/ingestion.md` (new)** — full design doc:
   - Why per-section splitting (retrieval intents, atomic units)
   - Why 400-token budget (512 cap, 22% headroom, tokenizer variance)
   - Breadcrumb + filename convention with full grammar
   - Three-layer token validation explained
   - Diagram of the end-to-end pipeline (reuses the ASCII diagram above)
   - Troubleshooting: what to do if `ingest-report.json` shows failures, how to interpret the token distribution, how to share logs
   - Links from README → here
4. **`docs/architecture.md` (new, short)** — one-page map of SDK layers (CLI → SDK → transport → mock/real) + where `pais.dev.*` fits in. Answers "where does X live?" for new contributors.
5. **`.env.example`** — add `# PAIS_INDEX_CHUNK_SIZE=512` / `# PAIS_INDEX_CHUNK_OVERLAP=64` as commented hints with the correct values for this use case.
6. **Docstrings** — every new module + public function gets a one-line docstring stating *what* and *why* (consistent with existing SDK style). No wall-of-text docstrings.
7. **`CHANGELOG.md` (new)** — start the changelog with the v0.1.0 entry (what shipped in the SDK foundation) and a v0.2.0 entry for this ingestion pipeline.

## Out of scope (deferred)

- Incremental re-ingestion (detect changed suite files and re-upload only those). Phase 2.
- Suite summary docs (explicitly rejected by user).
- Server-side metadata / tag filtering — not supported by PAIS.
- Live-PAIS verification with 300 real suites — user will run that separately; logs will be shared back for troubleshooting via the existing structured-logging path.

## Files changed / added (summary)

**Code (new):**
- `src/pais/dev/__init__.py`
- `src/pais/dev/markdown.py` — heading parser
- `src/pais/dev/token_budget.py` — tokenizer wrapper + `BUDGET=400`
- `src/pais/dev/split_suite.py` — splitter, slugifier, sub-split, budget enforcement
- `src/pais/dev/ingest.py` — file + directory walkers, worker pool, report writer
- `src/pais/cli/dev.py` — `pais-dev` typer group

**Config:**
- `pyproject.toml` → add `tokenizers>=0.15` to `[project.optional-dependencies] dev`; add `pais-dev = "pais.cli.dev:main"` to `[project.scripts]`
- `.gitignore` → `out/`, `ingest-report.json`, `.cache/` entries
- `.env.example` → add commented `PAIS_INDEX_CHUNK_SIZE` / `PAIS_INDEX_CHUNK_OVERLAP` hints

**Tests (new):**
- `tests/test_markdown_parser.py`
- `tests/test_token_budget.py`
- `tests/test_split_suite.py`
- `tests/test_ingest.py`
- `tests/test_cli_dev.py`

**Docs (new + updated):**
- `README.md` — new "Ingest test suites" section + install note + content-hygiene warning + idempotency note
- `CONTRIBUTING.md` — `tokenizers` optional dep note, `pais-dev` commands, dev-extras requirement for some tests
- `docs/ingestion.md` (new) — full design doc
- `docs/architecture.md` (new) — one-page layer map
- `CHANGELOG.md` (new) — v0.1.0 backfill + v0.2.0 entry for this iteration

**CI:**
- `.github/workflows/ci.yml` → install the `dev` extras so `tokenizers` is available for tokenizer-dependent tests (already uses `uv sync --all-extras`; verify it covers this).

## Implementation TODO

**Code:**
1. [ ] Add `tokenizers>=0.15` to `[project.optional-dependencies] dev` in `pyproject.toml` and wire `pais-dev` entrypoint
2. [ ] `pais.dev.token_budget` — tokenizer wrapper + `token_count()` + `BUDGET=400` + clear ImportError hint
3. [ ] `pais.dev.markdown` — heading parser (handle code fences, trailing whitespace, empty sections)
4. [ ] `pais.dev.split_suite` — atom grouping, slug sanitization, breadcrumb, filename convention, sub-split, token-budget enforcement, `SectionTooLarge` on pathological input
5. [ ] `pais.dev.ingest` — file + directory walkers, worker pool (default 4), SDK upload, progress bar, JSON report with token-distribution footer, ingest-time re-check
6. [ ] `pais.cli.dev` — typer group: `split-suite`, `ingest-suite`, `ingest-suites` with `--output {table,json,yaml}` and consistent exit codes

**Tests:**
7. [ ] `test_markdown_parser.py` — parsing edge cases + slug sanitization
8. [ ] `test_token_budget.py` — stable counts + missing-dep error message
9. [ ] `test_split_suite.py` — synthetic fixture, token-budget assertion, sub-split, `SectionTooLarge`
10. [ ] `test_ingest.py` — end-to-end split→upload→search via fake transport + Store; report shape; retry/failure modes
11. [ ] `test_cli_dev.py` — `pais-dev` commands via `CliRunner`, exit codes, JSON output
12. [ ] Optional real-fixture test gated on `~/Downloads/Access-Management.md` presence
13. [ ] Coverage ≥ 85% on `src/pais/dev/`

**Docs:**
14. [ ] `README.md` — add "Ingest test suites" section + install note + content-hygiene warning + idempotency note
15. [ ] `CONTRIBUTING.md` — tokenizers optional dep, `pais-dev` commands, dev-extras requirement
16. [ ] `docs/ingestion.md` (new) — full design doc
17. [ ] `docs/architecture.md` (new) — layer map
18. [ ] `.env.example` — commented chunk_size/overlap hints
19. [ ] `CHANGELOG.md` (new) — v0.1.0 + v0.2.0 entries
20. [ ] Module + public-function one-line docstrings

**Ship:**
21. [ ] CI workflow verified to install `[dev]` extras so tokenizer tests run
22. [ ] `ruff check` + `ruff format --check` + `mypy src` + `pytest -q` all green
23. [ ] Create feature branch `feat/ingestion-pipeline`, commit with conventional message, push, open PR on `github.com/dshahnaz/pais-sdk-cli`, wait for CI green, merge to `main`
