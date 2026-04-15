# Contributing

## Dev setup

```bash
# install uv if you don't have it
brew install uv    # or: curl -LsSf https://astral.sh/uv/install.sh | sh

uv sync --all-extras
```

## Run the full check locally

```bash
uv run ruff check
uv run ruff format --check
uv run mypy src
uv run pytest -q
```

## Run the mock server

```bash
uv run python -m pais_mock --host 127.0.0.1 --port 8080
```

Hit it with the CLI:

```bash
export PAIS_MODE=http
export PAIS_BASE_URL=http://127.0.0.1:8080/api/v1
export PAIS_AUTH=none
uv run pais kb list
```

## Project layout

- `src/pais/` — SDK (models, transport, auth, resources, client, cli)
- `src/pais/dev/` — developer-time helpers for the test-suite ingestion pipeline (splitter, token-budget checker, batch uploader)
- `src/pais_mock/` — in-memory backend + FastAPI app, shares `pais.models`
- `tests/contract/` — round-trip tests with PAIS-shaped JSON fixtures
- `tests/` — unit + integration tests (run against both fake transport and real uvicorn mock)
- `docs/` — design docs (`ingestion.md`, `architecture.md`)

## CLIs

- `pais` — user-facing: `kb`, `index`, `agent`, `mcp`, `models`, `mock serve`
- `pais-dev` — dev-facing: `split-suite`, `ingest-suite`, `ingest-suites`

## Tokenizer dependency

`pais-dev` uses `tokenizers` (HuggingFace) to measure chunks against PAIS's 512-token limit with the exact same tokenizer the server uses (`BAAI/bge-small-en-v1.5`). It lives under the `[dev]` optional-dependencies group, so installing with `uv sync --all-extras` is required to run `pais-dev` or the tests that touch `pais.dev.*`. First run downloads ~10 MB vocab into `~/.cache/huggingface/`; subsequent runs are offline.

## Commits

Use present-tense, imperative subjects (`add X`, `fix Y`, `refactor Z`). No gratuitous emoji.
