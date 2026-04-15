# pais-sdk-cli

Contract-first Python SDK + CLI for **VMware Private AI Service (PAIS)**, with a bundled mock server for offline development. Build and test against PAIS APIs without a live host; switch to a real instance via config.

## Quickstart

```bash
# install
uv sync --dev

# run the mock server
uv run python -m pais_mock &

# use the CLI against the mock
export PAIS_MODE=http
export PAIS_BASE_URL=http://localhost:8080/api/v1
export PAIS_AUTH=none
uv run pais kb list
```

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
