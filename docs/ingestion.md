# Ingestion (v0.7.0)

`pais ingest <kb_ref>:<index_ref> <path>` is the generic data-feed command. It looks up the splitter declared on the target index in your TOML config (or `--splitter <kind>` for one-off use), runs that splitter over `path` (file or directory), and uploads the resulting chunks to PAIS.

v0.7.0 is a **breaking simplification**: the generic splitters (`passthrough`, `text_chunks`, `markdown_headings`, `test_suite_md`) were removed. The built-in surface is now two test-suite splitters tuned to the user's PAIS embedding models. If you need a different splitter shape, run `pais splitters new <kind>` to scaffold one.

## Splitter registry (built-ins)

| kind | summary | target embeddings model | chunk_size | chunk_overlap |
|---|---|---|---|---|
| `test_suite_bge` | Per-test-case chunks with breadcrumb; tuned for bge-small-en-v1.5 | `BAAI/bge-small-en-v1.5` | 512 | 64 |
| `test_suite_arctic` | Per-test-case chunks with breadcrumb; tuned for arctic-embed-m-v2.0 | `Snowflake/snowflake-arctic-embed-m-v2.0` | 2048 | 256 |
<!-- splitters-table-end -->

Both splitters emit the same chunk format and filename convention. What differs is the **per-chunk token budget** (400 for bge, 1500 for arctic) and the recommended index `chunk_size` / `chunk_overlap`. Pick the variant that matches your index's `embeddings_model_endpoint`.

## The problem the test-suite splitters solve

Test-suite markdown files are highly structured:

```
# SuiteName
## Overview / Deployment Information / Components
## Test Coverage
   ### testCaseOne
   ### testCaseTwo
   ...
## Technology Stack
```

An agent querying the KB must answer three kinds of questions:

1. "What does `testEditUserRole` validate?" → wants **one test-case section**
2. "Which suite tests role creation?" → wants a **suite-level overview**
3. "Which tests depend on `createObjectScopeTest`?" → wants sections mentioning that name

### Two layers of chunking

Context can be lost twice:

| Layer | Where | What |
|---|---|---|
| A | our splitter (client-side) | Slices the `.md` into `SplitDoc`s (uploaded documents) |
| B | PAIS index (server-side) | Further slices each upload at `chunk_size` tokens with overlap |

If Layer B splits a test case mid-body, the bottom half becomes a naked `**Key Operations**: - ...` fragment with no suite or case identifier — the embedding vector loses the `Access-Management / testCreateUserRole` anchor and retrieval misses. **The splitter's job is to emit pre-sized, self-identifying chunks that survive Layer B.**

### The solution: atomic per-case chunks with breadcrumb

Each emitted chunk looks like:

```markdown
# Suite: Access-Management
# Testbed: vrops-1slice-config-ph | Components: Ops, VIDB

### testCreateUserRole

**Purpose**: Tests the creation of a new user-defined role...

**Dependencies**: Depends on `testGetAllRoles`

**Validations**:
- Confirms successful role creation via the createUserRole API
- ...
```

The breadcrumb is **≤ 60 tokens** — keeps total chunk size under the index's `chunk_size`, so Layer B doesn't re-split it. The breadcrumb lives **inside the chunk body** (not in metadata) because PAIS indexes only preserve `origin_name`; everything else must be in the text for the embedding to capture it.

### Optional: Anthropic contextual retrieval

Both splitters accept `with_context_llm = true` (TOML) or `--with-context-llm` (CLI). When enabled, each chunk gets a one-sentence LLM-generated description of its role in the document, prepended under the breadcrumb. This is the [Anthropic contextual-retrieval technique](https://www.anthropic.com/news/contextual-retrieval) — 49 % fewer retrieval failures in Anthropic's benchmarks, at a cost of one Claude Haiku call per chunk with prompt caching (~$1-3 for 300 suites).

Install the extra: `pip install 'pais-sdk-cli[contextual]'` and export `ANTHROPIC_API_KEY`.

## Filename convention

```
<SuiteSlug>__<order:02d>__<SectionSlug>[__pN].md
```

- `<SuiteSlug>`: H1 title with non-`[A-Za-z0-9_-]` chars collapsed to `-`.
- `<order>`: `00` for the overview chunk, then `01`, `02`, ... for test cases in source order.
- `<SectionSlug>`: `overview` for the overview chunk, otherwise the test-case H3 slug.
- `__pN`: only when a single test case exceeded the token budget and was sub-split.

`group_key = "<SuiteSlug>__"` — `pais ingest --replace` deletes all prior chunks for a suite before re-uploading. Untouched suites stay.

## Inspect + preview from the CLI

```bash
pais splitters list                                          # compact: kind + summary
pais splitters list -v                                       # adds input + chunk_size + unit
pais splitters show test_suite_bge                           # full meta + options + suggested index config

# Dry-run preview — shows distribution + suggested index config, no upload
pais splitters preview test_suite_bge ~/suites/foo.md

# Preview + dump every chunk to disk so you can open and eyeball each one
pais splitters preview test_suite_bge ~/suites/foo.md --dump /tmp/preview

# Preview + print each chunk header + first 200 chars to stdout
pais splitters preview test_suite_bge ~/suites/foo.md --show-all
```

`pais splitters show <kind>` and `preview` both display a **Recommended index config for this splitter** footer with the exact `embeddings_model_endpoint`, `chunk_size`, and `chunk_overlap` that splitter was tuned for. Use these values when creating the index.

## Pre-flight check at ingest time

When `pais ingest` runs, it compares the splitter's recommended config against the target index and emits a warning (non-blocking) if they disagree:

- `splitter 'test_suite_bge' targets embeddings_model='BAAI/bge-small-en-v1.5' but the index uses 'Snowflake/snowflake-arctic-embed-m-v2.0' - retrieval quality may degrade`
- `index chunk_size=256 is smaller than the splitter's recommended 512 - chunks may be re-split mid-body, losing breadcrumb context`

The ingest still runs; the warning is there to catch the common mistake of pointing the wrong splitter at an index before retrieval quality tells you.

## Scaffold a new splitter — `pais splitters new <kind>`

```bash
pais splitters new widget_html
# Prompts:
#   One-line summary (≤ 70 chars): Chunks Widget HTML docs at <section> boundaries
#   Input type: widget HTML files
#   Example input path: ~/widgets/foo.html
#   Chunk size unit (tokens/chars/file) [tokens]:
#   Target embeddings model (blank to skip): BAAI/bge-small-en-v1.5
#   Suggested index chunk_size (tokens): 512
#   Suggested index chunk_overlap (tokens): 64
#
# Writes:
#   src/pais/ingest/splitters/widget_html.py    (skeleton with TODO markers)
#   tests/test_splitter_widget_html.py          (registration + meta stubs)
#   updates src/pais/ingest/splitters/__init__.py (adds the import)
#   appends a row to docs/ingestion.md          (via <!-- splitters-table-end -->)
```

Use `--dry-run` to preview the generated files without writing. Use `--yes` to overwrite existing files.

## TOML config

```toml
[profiles.default.knowledge_bases.test_suites]
name = "test-suites-kb"

  [[profiles.default.knowledge_bases.test_suites.indexes]]
  alias = "main"
  name = "test-suites-index"
  embeddings_model_endpoint = "BAAI/bge-small-en-v1.5"
  chunk_size = 512
  chunk_overlap = 64

    [profiles.default.knowledge_bases.test_suites.indexes.splitter]
    kind = "test_suite_bge"
    # Optional:
    # max_case_tokens = 400
    # emit_overview_chunk = true
    # with_context_llm = false
    # context_llm_model = "claude-haiku-4-5-20251001"
```

Running `pais ingest test_suites:main ~/suites/ --replace` walks every `.md` under `~/suites/`, splits, and uploads.

## Re-ingest cleanly with `--replace`

For each file, the ingester:
1. Computes `group_key = "<SuiteSlug>__"`.
2. Deletes existing documents in the index whose `origin_name` starts with that prefix.
3. Uploads the freshly split chunks.

**Caveat**: `--replace` requires PAIS to expose `DELETE /documents/{id}`. If your deployment doesn't, the ingester aborts with a clear error pointing you at `pais index purge --strategy recreate` (which drops + recreates the entire index, changing its id).

## Related

- Splitter sources: `src/pais/ingest/splitters/{test_suite_bge,test_suite_arctic,_test_suite_core}.py`
- Scaffolder: `src/pais/cli/splitters_new_cmd.py`
- Preview: `src/pais/cli/_splitter_preview.py`
- Tests: `tests/test_splitter_test_suite_core.py`, `tests/test_splitter_test_suite_bge.py`, `tests/test_splitter_test_suite_arctic.py`, `tests/test_splitter_preview.py`, `tests/test_splitters_new_cmd.py`
- Architecture: [`architecture.md`](architecture.md)
