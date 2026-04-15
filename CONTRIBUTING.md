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
- `src/pais_mock/` — in-memory backend + FastAPI app, shares `pais.models`
- `tests/contract/` — round-trip tests with PAIS-shaped JSON fixtures
- `tests/` — unit + integration tests (run against both fake transport and real uvicorn mock)

## Commits

Use present-tense, imperative subjects (`add X`, `fix Y`, `refactor Z`). No gratuitous emoji.
