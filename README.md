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

## Ingest test suites

Feed ~300 structured markdown test-suite files into a PAIS KB with budget-safe chunking. Each suite file is split per-section, prefixed with a breadcrumb header (suite + section name + kind), and uploaded as one document per section. Every emitted file is validated against a 400-token cap (measured with the same `BAAI/bge-small-en-v1.5` tokenizer PAIS uses) so one file = one chunk — no silent server-side re-splitting. See [`docs/ingestion.md`](docs/ingestion.md) for the full design.

```bash
# install dev extras (adds the tokenizers dep used by the splitter)
uv sync --all-extras

# 0. create KB + index (chunk_size is in tokens, not chars)
pais kb create --name test-suites --output json       # → kb_id
pais index create <kb_id> --name ts-idx \
    --embeddings-model BAAI/bge-small-en-v1.5 \
    --chunk-size 512 --chunk-overlap 64 --output json # → ix_id

# 1. dry-run: split one file to disk and inspect
pais-dev split-suite ./suites/Access-Management.md --out ./out/

# 2. split + upload one suite
pais-dev ingest-suite ./suites/Access-Management.md --kb <kb_id> --index <ix_id>

# 3. bulk: walk a directory, parallelize, write a JSON report
pais-dev ingest-suites ./suites/ --kb <kb_id> --index <ix_id> \
    --workers 4 --report ./ingest-report.json

# 4. wait for indexing
pais index wait <kb_id> <ix_id>
```

**Content hygiene**: bodies are uploaded as-is. Scrub internal hostnames / IPs / credentials from suite files before ingesting into any shared PAIS deployment — the structured logger redacts secret-looking *keys* but cannot sanitize arbitrary prose.

**Idempotency (current limitation)**: re-running `ingest-suites` against the same directory creates duplicates — PAIS assigns new `document_id`s to the same `origin_name`s. For a clean re-ingest, delete the KB and recreate it. A `--replace` flag is planned.

## Cleanup & cancel

Destructive ops require either a TTY confirmation prompt or `--yes` / `-y`. They refuse to run in scripts (non-TTY) without `--yes`.

```bash
# delete a whole KB (cascades indexes + documents)
pais kb delete kb_xxx --yes

# keep the KB, drop every document under every index in it
pais kb purge kb_xxx --yes

# keep the index, drop its documents
pais index purge kb_xxx idx_yyy --yes

# delete one index entirely
pais index delete kb_xxx idx_yyy --yes

# cancel a running indexing job
pais index cancel kb_xxx idx_yyy --yes
```

Each cleanup/cancel command takes `--strategy {auto,api,recreate}`:

- **`api`** — try the obvious REST verb (`DELETE /documents/{id}` for purge, `DELETE /active-indexing` for cancel). Fails fast if the PAIS deployment doesn't expose it.
- **`recreate`** — delete the index entirely and recreate it with the same config. Always works but **the new index gets a different `id`** — you'll need to re-link any agents pointing at the old one. The CLI prints a warning when this happens.
- **`auto`** (default) — try `api` first, fall back to `recreate` on 404/405.

### Re-ingest cleanly

`pais-dev ingest-suites --replace` deletes only the documents whose `origin_name` belongs to the suites being re-uploaded; everything else stays:

```bash
pais-dev ingest-suites ./changed-suites/ --kb kb_xxx --index idx_yyy --replace
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
