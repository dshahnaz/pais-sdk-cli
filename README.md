# pais-sdk-cli

Contract-first Python SDK + CLI for **VMware Private AI Service (PAIS)**, with a bundled mock server for offline development. Build and test against PAIS APIs without a live host; switch to a real instance via config.

## Install

Not on PyPI yet — install straight from GitHub. Both the `pais` and `pais-dev` commands are wired as console scripts and land on your `PATH`.

```bash
# pip — latest main
pip install "git+https://github.com/dshahnaz/pais-sdk-cli.git"

# pip — pinned to a tag/commit (recommended for reproducibility)
pip install "git+https://github.com/dshahnaz/pais-sdk-cli.git@v0.1.0"

# pip — include dev extras (adds the HuggingFace tokenizers dep needed by `pais-dev`)
pip install "git+https://github.com/dshahnaz/pais-sdk-cli.git#egg=pais-sdk-cli[dev]"

# uv — into an isolated tool environment (recommended for CLI users)
uv tool install "git+https://github.com/dshahnaz/pais-sdk-cli.git"
uv tool install --with "pais-sdk-cli[dev]" "git+https://github.com/dshahnaz/pais-sdk-cli.git"

# pipx — same idea
pipx install "git+https://github.com/dshahnaz/pais-sdk-cli.git"
```

Verify:

```bash
pais --help
pais-dev --help
```

For local development (clone + editable install) see [CONTRIBUTING.md](CONTRIBUTING.md).

## Quickstart

```bash
# run the mock server
python -m pais_mock &

# use the CLI against the mock
export PAIS_MODE=http
export PAIS_BASE_URL=http://localhost:8080/api/v1
export PAIS_AUTH=none
pais kb list
```

## Interactive mode

Run `pais` (no args) in a terminal and it opens with a smart landing screen:

```text
$ pais
profile=lab  ·  mode=http (real PAIS)
3 KBs  ·  5 indexes  ·  2 agents  · 0 drift

recommended: 💬  Chat with an agent

> 💬  Chat with an agent
  🤖  Set up a chat agent over my docs
  📦  Provision KB + index (no agent)
  🔧  Apply pending TOML config
  📥  Ingest data into an index
  🔎  Search an index (no LLM)
  🗑  Cleanup (delete KB / index / agent)
  📋  all commands…
```

The recommended workflow is chosen from your env state (no agents → "Set up an agent"; drift detected → "Apply pending TOML"). Each workflow walks you through the steps with **pick-or-create lists** (showing existing items + `+ create new`), **single-screen reviews** of all defaults (with one-line hints — e.g. `chunk_size 512  ↑ tokens, not chars`), and a **"what next?"** menu after success.

**Set up an agent** is the headline flow:
```
🤖 Set up a chat agent over my docs.
1. Pick or create a KB
2. (optional) save it as alias `prod_docs` in pais.toml
3. Pick or create an index under it
4. (optional) save the index alias too
5. Create the agent (index_id + index_top_n, doc-aligned)
6. → Ingest data into this index now (recommended — index is empty)
   💬 Chat with it
   📊 View `pais status`
   ✅ Done
```

Destructive ops use **type-to-confirm** (you type the resource name to proceed). `--quick-confirm` / `-Q` falls back to `y/N` for power users.

`pais shell` opens the menu explicitly. To opt out of the bare-`pais` trigger, pass `--no-interactive` or set `PAIS_NONINTERACTIVE=1`. Non-TTY callers (pipes, scripts, CI) always print the help banner, never the menu. Pick `📋  all commands…` from the landing screen for the full v0.5-style flat command list.

## Persistent config

Tired of `export PAIS_*` on every shell? Drop a TOML config file with named profiles:

```bash
pais config init                  # writes ~/.pais/config.toml with comments
pais config init --project        # writes ./pais.toml in the current dir
pais config show --profile lab    # print effective settings (secrets redacted)
pais config path                  # which file + profile resolve right now
```

Example file:

```toml
# ~/.pais/config.toml  (or ./pais.toml — project wins over global)
default_profile = "lab"

[profiles.lab]
mode = "http"
base_url = "https://pais.internal/api/v1"
auth = "none"
verify_ssl = false

[profiles.prod]
mode = "http"
base_url = "https://pais.example.com/api/v1"
auth = "oidc_password"
oidc_issuer = "https://pais.example.com"
client_id = "pais-cli"
username = "alice"
# password / client_secret / bearer_token are REJECTED here — env vars only.
```

Then every command picks it up:

```bash
pais --profile lab kb list
pais --profile prod agent chat agent_xx "hello"
# or set PAIS_PROFILE=lab once for the whole shell
```

Precedence (highest first): CLI flag → `PAIS_*` env var → config file → defaults. Discovery order for the file: `--config <path>` → `PAIS_CONFIG` → `./pais.toml` → `~/.pais/config.toml`.

## Three runbooks

### 1. Mock mode (no real host)

```bash
export PAIS_MODE=mock
uv run pais kb create --name demo
uv run pais kb list
```

Tests use the in-process fake transport; no server needed.

### 2. Real PAIS — internal network, no auth

```bash
export PAIS_MODE=http
export PAIS_BASE_URL=https://pais.internal/api/v1
export PAIS_AUTH=none
export PAIS_VERIFY_SSL=false
uv run pais kb list
```

### 3. Real PAIS — OIDC

```bash
export PAIS_MODE=http
export PAIS_BASE_URL=https://pais.example.com/api/v1
export PAIS_AUTH=oidc_password
export PAIS_OIDC_ISSUER=https://pais.example.com
export PAIS_CLIENT_ID=... PAIS_USERNAME=... PAIS_PASSWORD=...
uv run pais kb list
```

## Declarative config & `pais kb ensure`

Declare KBs + indexes + their splitters in TOML, then operate on short aliases instead of UUIDs.

```toml
# ~/.pais/config.toml  (or ./pais.toml — project wins)
[profiles.lab]
mode = "http"
base_url = "https://10.160.11.45/api/v1"
auth = "none"
verify_ssl = false

[profiles.lab.knowledge_bases.test_suites]
name = "mops-permanent-test-suites"
data_origin_type = "LOCAL_FILES"

  [[profiles.lab.knowledge_bases.test_suites.indexes]]
  alias = "main"
  name = "ts-idx"
  embeddings_model_endpoint = "BAAI/bge-small-en-v1.5"
  chunk_size = 512
  chunk_overlap = 64

    [profiles.lab.knowledge_bases.test_suites.indexes.splitter]
    kind = "test_suite_md"   # H1/H2/H3 atomic sections + breadcrumb header
    budget_tokens = 400

  [[profiles.lab.knowledge_bases.test_suites.indexes]]
  alias = "raw"
  name = "ts-raw"
  embeddings_model_endpoint = "BAAI/bge-small-en-v1.5"

    [profiles.lab.knowledge_bases.test_suites.indexes.splitter]
    kind = "passthrough"     # upload files as-is; PAIS handles splitting
```

Then:

```bash
pais --profile lab kb ensure   # create anything missing on the server (idempotent)
pais kb list --with-counts     # see KBs with index + document totals
pais kb show test_suites       # full KB detail with per-index breakdown
```

`pais kb ensure` is idempotent. Re-run after editing the TOML — it adds new KBs/indexes and warns about server-side mismatches PAIS doesn't expose updates for. `--dry-run` previews; `--prune --yes` deletes server-side resources not in the TOML (per-item confirmation).

## Ingest data

Generic `pais ingest` runs the splitter declared on the target index over any file or directory.

```bash
# 0. ensure the KB + index exist (one-time setup from config)
pais --profile lab kb ensure

# 1. ingest a directory of suite markdown files (uses test_suite_md splitter from config)
pais ingest test_suites:main ./suites/

# 2. ingest some PDFs / plain text into the same KB but a different index
pais ingest test_suites:raw ./pdfs/

# 3. re-ingest only changed suites; other suites in the index stay
pais ingest test_suites:main ./changed/ --replace

# 4. preview without uploading
pais ingest test_suites:main ./suites/ --dry-run

# 5. one-off override of the splitter
pais ingest test_suites:main ./README.md --splitter markdown_headings

# 6. wait for indexing
pais index wait test_suites:main
```

UUIDs work everywhere aliases do — `pais ingest <kb_uuid>:<idx_uuid> ./files/` is fine for ad-hoc use.

### Built-in splitters

| kind | summary | input | typical chunk |
|---|---|---|---|
| `test_suite_md` | Atomic per-section split for H1/H2/H3 test-suite markdown | structured markdown (H1=suite, H2=section, H3=subsection) | ≈ 400 tokens (~1.5 KB English) |
| `markdown_headings` | Generic markdown split at a configurable heading level | any markdown with headings | variable — one chunk per H2 (or H3) |
| `passthrough` | Upload each file as-is; PAIS handles all splitting | any file (binary OK) | = file size (1 chunk per file) |
| `text_chunks` | Sliding character window with configurable overlap | any UTF-8 text (logs, plain text) | 1500 chars (≈ 375 tokens English) with 100-char overlap |

Discover and inspect them from the CLI:
```bash
pais splitters list                        # compact (kind + summary)
pais splitters list -v                     # adds input + chunk size + unit
pais splitters show test_suite_md          # rich panel: input/algorithm/output/options/notes
pais splitters preview test_suite_md ~/Downloads/Access-Management.md
                                           # dry-run: chunk count + size distribution
                                           # in BOTH tokens and chars + sample chunk
```

The `preview` command is the fastest way to answer "how would this splitter chop my file?" before spending an upload.

**Content hygiene**: bodies are uploaded as-is. Scrub internal hostnames / IPs / credentials from input files before ingesting into a shared PAIS deployment — the structured logger redacts secret-looking *keys* but cannot sanitize arbitrary prose.

## Cleanup & cancel

Destructive ops require either a TTY confirmation prompt or `--yes` / `-y`. They refuse to run in scripts (non-TTY) without `--yes`.

All commands accept either an alias (from your config) or a UUID.

```bash
# delete a whole KB (cascades indexes + documents)
pais kb delete test_suites --yes

# keep the KB, drop every document under every index in it
pais kb purge test_suites --yes

# keep the index, drop its documents
pais index purge test_suites main --yes

# delete one index entirely
pais index delete test_suites main --yes

# cancel a running indexing job
pais index cancel test_suites main --yes
```

Each cleanup/cancel command takes `--strategy {auto,api,recreate}`:

- **`api`** — try the obvious REST verb (`DELETE /documents/{id}` for purge, `DELETE /active-indexing` for cancel). Fails fast if the PAIS deployment doesn't expose it.
- **`recreate`** — delete the index entirely and recreate it with the same config. Always works but **the new index gets a different `id`** — you'll need to re-link any agents pointing at the old one. The CLI prints a warning when this happens.
- **`auto`** (default) — try `api` first, fall back to `recreate` on 404/405.

### Re-ingest cleanly

`pais ingest --replace` deletes only the documents whose `origin_name` matches the splitter's `group_key` for each input file; everything else stays:

```bash
pais ingest test_suites:main ./changed-suites/ --replace
```

## Logging & troubleshooting

- Logs: `~/.pais/logs/pais.log` (rotating, 5MB × 3).
- Verbosity: `PAIS_LOG_LEVEL=DEBUG`.
- Secrets (`authorization`, `password`, `access_token`, `refresh_token`, ...) are redacted. Safe to share log files as-is.
- Every request carries a `request_id` that round-trips to the server as `X-Request-ID` for correlation.

## Architecture

```
CLI (typer) → SDK (resources) → Transport (httpx | fake) → PAIS host | mock server
```

Models in `src/pais/models/` are imported by both the SDK and the mock server — the mock serves the exact contract the SDK validates.

## License

MIT
