# Architecture

One-page map of where things live in this repo.

## Layers

```
┌───────────────────────────────────────────────────────────┐
│  CLI                                                      │
│  pais       → src/pais/cli/app.py   (kb, index, agent,…)  │
│  pais-dev   → src/pais/cli/dev.py   (split-suite, ingest) │
└────────────────────────┬──────────────────────────────────┘
                         │
┌────────────────────────▼──────────────────────────────────┐
│  SDK                                                      │
│  src/pais/client.py          PaisClient facade            │
│  src/pais/resources/*.py     one file per resource group  │
│  src/pais/models/*.py        contract-first pydantic      │
│  src/pais/dev/*.py           test-suite ingestion helpers │
│  src/pais/auth/*.py          none | bearer | oidc_password│
│  src/pais/logging.py         structlog + redaction        │
│  src/pais/errors.py          PaisError hierarchy          │
└────────────────────────┬──────────────────────────────────┘
                         │
┌────────────────────────▼──────────────────────────────────┐
│  Transport                                                │
│  src/pais/transport/httpx_transport.py   real HTTP        │
│  src/pais/transport/fake_transport.py    in-process       │
└────────────────────────┬──────────────────────────────────┘
                         │
          ┌──────────────┴──────────────┐
          ▼                             ▼
┌───────────────────┐       ┌────────────────────────────┐
│  Real PAIS host   │       │  Mock server               │
│  (internal net /  │       │  src/pais_mock/server.py   │
│   OIDC hosted)    │       │  src/pais_mock/state.py    │
└───────────────────┘       └────────────────────────────┘
```

## Strict one-way dependencies

- `cli` → `client` → `resources` → `transport`
- `dev` → `client` + `models` (does not reach into `cli` or `transport` directly)
- `pais_mock.state.Store` implements the `MockBackend` protocol used by both `FakeTransport` (in-process) and `pais_mock.server` (FastAPI). Models live in `pais.models/` and are imported by both sides, which is what makes the mock contract-identical to the SDK.

## Where to put new code

| Adding… | Goes in |
|---|---|
| A new PAIS endpoint (not yet covered) | new resource class under `src/pais/resources/` + models under `src/pais/models/` + matching mock routing in `src/pais_mock/state.py` |
| A new auth flow | `src/pais/auth/<flow>.py` implementing `AuthStrategy`, wired in `pais.client._build_auth` |
| A new preprocessing helper for ingestion | `src/pais/dev/` + CLI command in `src/pais/cli/dev.py` |
| A new CLI output format | `src/pais/cli/_output.py::render` |

## Tests

All layers have dedicated tests:

- Models — `tests/contract/` (round-trip fixtures, catches API drift)
- Transport — `tests/test_transport.py`, `tests/test_resiliency.py`
- Auth — `tests/test_auth.py`
- Resources — `tests/test_resources.py` (parametrized: fake transport + live uvicorn mock)
- CLI — `tests/test_cli.py`, `tests/test_cli_dev.py`
- Ingestion — `tests/test_markdown_parser.py`, `tests/test_token_budget.py`, `tests/test_split_suite.py`, `tests/test_ingest.py`
- Logging — `tests/test_logging.py`
- Integration seam — `tests/test_integration_seam.py` (proves external embed works without CLI)
