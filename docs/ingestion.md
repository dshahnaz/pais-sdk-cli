# Ingestion (v0.4+)

`pais ingest <kb_ref>:<index_ref> <path>` is the generic data-feed command. It looks up the splitter declared on the target index in your TOML config (or `--splitter <kind>` for one-off use), runs that splitter over `path` (file or directory), and uploads the resulting chunks to PAIS.

## Splitter registry (built-ins)

| kind | best for | options (TOML) |
|---|---|---|
| `test_suite_md` | structured test-suite markdown — atomic per-section files with breadcrumb headers | `budget_tokens` |
| `markdown_headings` | any markdown — split at H2 (default) or H3, optional breadcrumb | `heading_level`, `breadcrumb` |
| `passthrough` | PDFs, plain text, anything where PAIS should do its own splitting | — |
| `text_chunks` | plain text / logs — sliding-window chunker | `chunk_chars`, `overlap_chars` |

`pais splitters list` and `pais splitters show <kind>` print the registry and option JSON schemas.

Splitters live under `src/pais/ingest/splitters/` and self-register via the `@register_splitter` decorator. Adding a 5th is one file.

## Why `test_suite_md` exists (the original use case)

Each suite file is highly structured markdown:

## The problem

Each suite file is highly structured markdown:

```
# SuiteName
## Overview / Deployment Information / Components
## Test Coverage
   ### testCaseOne
   ### testCaseTwo
   …
## Technology Stack
```

An agent on top of the KB must answer three kinds of questions:

1. "What does `testEditUserRole` validate?" → wants **one test-case section**
2. "Which suite tests role creation?" → wants a **suite name**
3. "Which tests depend on `createObjectScopeTest`?" → wants sections mentioning that name

One-size chunking hurts all three. Sentence-splitting a whole suite bleeds test-case boundaries into neighbours; a coarse chunk mixes multiple unrelated tests.

## The solution: per-section atomic files

The splitter decomposes each suite file into **one file per logical section**:

```
Access-Management__05_overview__overview.md
Access-Management__10_test__testGetAllRoles.md
Access-Management__10_test__testCreateUserRole.md
…
Access-Management__20_tech_stack__tech_stack.md
```

Each emitted file has a **breadcrumb header** prepended:

```markdown
# Suite: Access-Management
## Section: testEditUserRole
## Kind: test

[original section body]
```

Every chunk PAIS returns carries the suite name and the section name *in its text*, regardless of which split it is. That's the retrieval signal.

## Why 400 tokens, not 512

The PAIS index is configured with `chunk_size: 512` **tokens** (not chars — documented behavior). The splitter's hard cap is **400 tokens** per emitted file, measured with the exact tokenizer the index uses (`BAAI/bge-small-en-v1.5` via HuggingFace `tokenizers`). That leaves a 112-token (22%) cushion against tokenizer variance. Real-world distribution on `Access-Management.md`:

```
13 sections total
min=115  p50≈175  max=254  (budget=400)
```

Every section comfortably fits in one chunk.

## What happens when a section exceeds 400 tokens

Rare in practice, but handled:

1. **Paragraph split** — section body split at blank lines, greedily packed into groups each fitting the budget. Each group becomes `…__part1.md`, `…__part2.md`, etc., each re-prefixed with the same breadcrumb.
2. **Sentence split** — fallback when a single paragraph alone overflows.
3. **`SectionTooLargeError`** — raised if a single indivisible sentence still exceeds 400 tokens. Indicates malformed input.

## Three-layer token validation

1. **Splitter-internal** — `src/pais/dev/split_suite.py` calls `token_count()` on every `rendered` file before returning. Over-budget → sub-split and re-measure.
2. **Unit test** — `tests/test_split_suite.py::test_every_emitted_section_fits_budget` asserts every emitted section ≤ 400 tokens. CI-blocking.
3. **Ingest-time re-check** — `ingest_file()` calls `_guard_budget()` before uploading. Belt-and-suspenders protection against a dev path that bypassed the splitter.

Optional 4th layer: when `~/Downloads/Access-Management.md` exists, `tests/test_split_suite.py::test_real_access_management_fixture` runs the same checks on the real file and prints the token distribution.

## Pipeline

```
┌────────────────┐   ┌───────────────┐   ┌─────────────────┐   ┌──────────┐
│  300 .md files │──▶│  splitter     │──▶│ ~3,600 section  │──▶│  PAIS    │
│  one per suite │   │  (pais.dev.   │   │ files           │   │  KB      │
│                │   │   split_suite)│   │ (header +       │   │  + index │
└────────────────┘   └───────────────┘   │  origin_name)   │   │          │
                                         └─────────────────┘   └──────────┘
                                                  │                  ▲
                                                  ▼                  │
                                         ┌─────────────────┐         │
                                         │ batch uploader  │─────────┘
                                         │ ThreadPool x 4  │
                                         │ → report.json   │
                                         └─────────────────┘
```

## Filename convention

```
<SuiteName>__<order>_<kind>__<SectionSlug>[__partN].md

order:  05=overview   10=test   20=tech_stack
kind:   overview | test | tech_stack
```

- `SuiteName`: verbatim H1 title with non-`[A-Za-z0-9_-]` chars replaced by `_`.
- `SectionSlug`: same sanitization applied to the H3 title.
- `partN` appended only when a section was sub-split.
- Lexicographic sort of filenames matches reading order.

The filename becomes `origin_name` in PAIS — the only durable metadata channel the API exposes.

## Batch upload behavior

- Walks `<root>` recursively for `*.md`.
- `--workers N` threads (default 4). Conservative for a single-node internal PAIS; tuneable up to 32.
- Per-suite failures are logged into `ingest-report.json` but don't stop the run.
- 5xx retries are handled by the existing SDK transport (exponential backoff + jitter).
- Report includes global token-count distribution (`min / p50 / p95 / max`) across all emitted sections.

## Re-ingest cleanly with `--replace`

PAIS does not dedupe on `origin_name`, so a plain re-ingest produces duplicates. Use the `--replace` flag:

```bash
pais-dev ingest-suites ./suites/ --kb kb_xxx --index idx_yyy --replace
```

For each suite file, the ingester:
1. Computes the suite slug from its H1 title (same algorithm the splitter uses).
2. Lists existing documents in the index and deletes only those whose `origin_name` starts with `<slug>__`.
3. Uploads the freshly split sections.

**Untouched suites stay**, so you can re-ingest only the files that changed. See [README cleanup section](../README.md#cleanup--cancel) for related ops (`kb purge`, `index purge`, `index cancel`) that share the same `--strategy {auto,api,recreate}` semantics.

**Caveat**: `--replace` requires PAIS to expose `DELETE /documents/{id}`. If your deployment doesn't, the ingester aborts with a clear error pointing you at `pais index purge --strategy recreate` (which drops + recreates the entire index, changing its id).

## Troubleshooting

**`ingest-report.json` shows failures**

Open the report, look for the `errors` array on the failing suite entry. Common causes:

- `SectionTooLargeError` → one section has a single sentence > 400 tokens. Reformat the source suite to break the sentence.
- `PaisServerError / 5xx` → transient PAIS issue; rerun. Persistent → share `~/.pais/logs/pais.log` for triage (already redacted).
- `ValueError: no H1 title found` → the suite file is missing the top-level `# SuiteName` line.

**Token distribution looks wrong**

Inspect the `token_distribution` footer of `ingest-report.json`. If `p95` is close to 400, the splitter is near its limit — suites are getting denser than expected. Lower the splitter's `BUDGET` constant to add more headroom, or adjust suite content.

**Share logs with the maintainers**

`~/.pais/logs/pais.log` is structured JSON with secrets redacted. Safe to paste verbatim. Each ingest run shares a single `request_id` across all its entries — grep for it to slice a single run out of the file.

## Related

- API reference: `src/pais/dev/split_suite.py`, `src/pais/dev/ingest.py`
- CLI: `pais-dev split-suite`, `pais-dev ingest-suite`, `pais-dev ingest-suites`
- Tests: `tests/test_split_suite.py`, `tests/test_ingest.py`, `tests/test_cli_dev.py`
- Architecture overview: [`architecture.md`](architecture.md)
