# CLAUDE.md — operational playbook for this repo

This file is auto-loaded when working in `pais-sdk-cli`. Read this before changing code.

## What this is

Python SDK + `pais` CLI for **VMware Private AI Service (PAIS)**, with a bundled mock server (`pais_mock`) for offline development. Public on GitHub: <https://github.com/dshahnaz/pais-sdk-cli>. Latest tag: `v0.4.0` (see `pyproject.toml`/`__version__` for current).

## Layout (one-pager)

- `src/pais/` — SDK
  - `client.py` — `PaisClient` facade
  - `resources/*.py` — one file per PAIS resource group (knowledge_bases, indexes, agents, …)
  - `models/*.py` — contract-first pydantic models, shared with the mock
  - `transport/{base,httpx_transport,fake_transport}.py` — Transport protocol + 2 impls
  - `auth/{base,none,bearer,oidc_password}.py` — pluggable auth strategies
  - `ingest/registry.py` + `ingest/splitters/*.py` — splitter plug-in system (4 built-ins)
  - `ingest/runner.py` — generic ingest pipeline (worker pool, JSON report)
  - `cli/app.py` — `pais` typer entrypoint
  - `cli/{ingest_cmd,ensure_cmd,config_cmd,_alias,_kb_show,_config_file,_profile_config,_output}.py`
  - `dev/{markdown,split_suite,token_budget,ingest}.py` — legacy helpers wrapped by `pais.ingest.splitters.test_suite_md`; `cli/dev.py` is now a removal-redirect shim
  - `config.py` — pydantic-settings: env > config-file profile > .env > defaults
  - `errors.py`, `logging.py`
- `src/pais_mock/{server,state,behaviors}.py` — FastAPI mock + in-memory `Store` (implements the same protocol that `FakeTransport` consumes; tests + the standalone `python -m pais_mock` server share it)
- `tests/` — 155+ tests across contract, transport, auth, resources, CLI, ingest, alias resolver, ensure, etc. Coverage gate: ≥ 85 % on touched modules; we sit at ~91 %.
- `docs/` — `ingestion.md`, `architecture.md`, `migration-0.3-to-0.4.md`, `v0.4-plan.md`
- `.github/workflows/ci.yml` — matrix Python 3.10 / 3.11 / 3.12

## Verified PAIS API constraints (don't re-discover)

These shape the design — never assume otherwise without re-checking the docs:

1. `chunk_size` and `chunk_overlap` are **tokens**, not characters.
2. **No documented per-document DELETE**, no batch DELETE, no cancel-indexing endpoint. Cleanup ops use a probe-then-fallback pattern (`DELETE /…/documents/{id}` or `DELETE /active-indexing` first; on 404/405, delete-and-recreate the index — which **changes the index_id**).
3. **No metadata / tag fields on documents.** The only durable label is `origin_name` (the uploaded filename).
4. **No metadata-filtered search.** Filtering happens client-side or in the agent prompt.
5. The user's prod PAIS is internal-network, no-auth (`PAIS_AUTH=none`), self-signed TLS (`PAIS_VERIFY_SSL=false`).

## Tooling

- `uv` for env + scripts. Install all extras: `uv sync --all-extras`.
- Lint + format: `ruff` (config in `pyproject.toml`).
- Type-check: `mypy` strict on `src/`.
- Tests: `pytest`. Several tests spin up uvicorn against the mock; that's normal.
- Splitter token counts use HuggingFace `tokenizers` (optional `[dev]` extra).

## Standard commands

```bash
uv sync --all-extras
uv run ruff check
uv run ruff format --check       # use `uv run ruff format` to fix
uv run mypy src
uv run pytest -q
uv run pytest --cov=src/pais -q  # with coverage
```

## Release / publish ritual (followed for v0.3.0, v0.3.1, v0.4.0 — keep doing this)

1. Branch: `git checkout -b feat/<short-name>` (or `fix/…`, `chore/…`).
2. Make changes. Add tests. Update docs (every user-facing change → README + CHANGELOG).
3. Local gates must pass: `ruff check && ruff format --check && mypy src && pytest -q`.
4. Bump the version in **two places**: `pyproject.toml` `[project] version` AND `src/pais/__init__.py` `__version__` (they MUST match — `pip install --upgrade` silently no-ops if the version doesn't increase).
5. CHANGELOG: add a new top section at the top. **Breaking changes get an explicit `### ⚠️ Breaking changes` header.**
6. Commit with conventional subject (`feat:`, `fix:`, `chore:`, `docs:`). Include the `Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>` trailer.
7. Push branch + open PR with `gh pr create`. PR body has Summary + Test plan checklist.
8. Watch CI: `gh pr checks <num> --repo dshahnaz/pais-sdk-cli --watch`. Must be green on 3.10/3.11/3.12 before merge.
9. Squash-merge: `gh pr merge <num> --repo dshahnaz/pais-sdk-cli --squash --delete-branch`.
10. Sync main: `git checkout main && git pull`.
11. Tag: `git tag -a v<X.Y.Z> -m "v<X.Y.Z> — <one-line summary>"` then `git push origin v<X.Y.Z>`.
12. GitHub release: `gh release create v<X.Y.Z> --title "..." --notes "..."`. For breaking-change releases, put the migration call-out at the **top** of the notes.

After release, users upgrade via:
```bash
pip install --upgrade "git+https://github.com/dshahnaz/pais-sdk-cli.git@v<X.Y.Z>"
```

## Conventions enforced in code

- `from __future__ import annotations` on every module (we support Python 3.10+).
- `pais.dev.*` is **legacy / internal**. New code goes in `pais.ingest.*` or `pais.cli.*`. Don't add new public surface to `pais.dev`.
- New CLI commands live in their own file under `pais.cli/<name>_cmd.py` and get wired in `app.py`. Use module-level `typer.Option` constants to satisfy ruff B008.
- New splitters: subclass with `kind: ClassVar[str]`, `options_model: ClassVar[type[BaseModel]]`, `__init__(options)`, `split(path) -> Iterator[SplitDoc]`, `group_key(path) -> str`. Register with `@register_splitter`. Add a row to the table in README + `docs/ingestion.md`.
- Destructive CLI ops require `--yes`/`-y` AND prompt on TTY; refuse non-TTY without `--yes`.
- Every alias-accepting command goes through `_resolve_kb` / `_resolve_index` — UUIDs pass through, declared aliases resolve to UUIDs (cached at `~/.pais/aliases.json`).
- TOML config validation errors must point at the exact TOML path. Use pydantic models in `_profile_config.py`; never raw dicts.
- Logs are structured JSON via `pais.logging`. Secrets (`password`, `authorization`, `access_token`, …) are auto-redacted. Log lines must include `request_id`.

## Mock server (use this for any local testing)

```bash
uv run python -m pais_mock --port 8080 &
export PAIS_MODE=http
export PAIS_BASE_URL=http://127.0.0.1:8080/api/v1
export PAIS_AUTH=none
pais kb list
```

Or set `PAIS_MODE=mock` (no HTTP, in-process `FakeTransport`). Settings precedence: CLI flag > env > `~/.pais/config.toml` profile > defaults.

## How to plan / propose changes

- For any non-trivial change: enter Plan Mode, write the plan to `~/.claude/plans/<plan>.md`, include a **Safety review** section (risks → mitigations) and a **flat one-line-per-task TODO** at the end.
- Verify against PAIS docs before designing anything API-shaped (the user has called this out explicitly — don't skip).
- Plans are also copied into `docs/<feature>-plan.md` for the repo when the user asks.

## What NOT to do

- Don't bump only `pyproject.toml` and forget `__init__.py` (or vice versa) — `pip install --upgrade` will silently no-op.
- Don't put secrets in `pais.toml` — the loader rejects `password`, `client_secret`, `bearer_token` at parse time.
- Don't false-positive `--replace` matches: each splitter owns its `group_key`. Runner does `origin_name.startswith(group_key)`. Convention: most splitters end `group_key` with `__`; `passthrough` uses the full filename for exact match.
- Don't `pais kb ensure --prune` casually — it deletes server-side resources not in the TOML. `--yes` plus per-item TTY confirmation gate is mandatory.
- Don't hand-edit the alias cache `~/.pais/aliases.json` — use `pais alias clear` instead.

## When in doubt

Read the latest plan in `docs/v0.4-plan.md`, then `docs/ingestion.md`, then `docs/architecture.md`. The plan files include rationale and trade-offs that aren't repeated in code comments.
