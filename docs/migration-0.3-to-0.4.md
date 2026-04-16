# Migrating from v0.3 to v0.4

v0.4 collapses the two-binary CLI into one (`pais`) and replaces the test-suite-specific `pais-dev ingest-suites` with a generic `pais ingest` that picks the splitter from your TOML config.

## Command translations

| v0.3 (`pais-dev`) | v0.4 (`pais`) |
|---|---|
| `pais-dev split-suite f.md --out ./out/` | `pais ingest K:I f.md --dry-run` (preview, no upload; chunks reflected in `ingest-report.json`) |
| `pais-dev ingest-suite f.md --kb K --index I` | `pais ingest K:I f.md` |
| `pais-dev ingest-suites d/ --kb K --index I` | `pais ingest K:I d/` |
| `pais-dev ingest-suites d/ --kb K --index I --replace` | `pais ingest K:I d/ --replace` |
| `pais-dev ingest-suites d/ --kb K --index I --workers 8` | `pais ingest K:I d/ --workers 8` |

`K` and `I` may be aliases (declared in your TOML config) or raw UUIDs.

## Splitter selection

In v0.3 the splitter was always the test-suite splitter. In v0.4 each index declares its splitter in TOML:

```toml
[profiles.lab.knowledge_bases.test_suites]
name = "mops-permanent-test-suites"

  [[profiles.lab.knowledge_bases.test_suites.indexes]]
  alias = "main"
  name = "ts-idx"
  embeddings_model_endpoint = "BAAI/bge-small-en-v1.5"

    [profiles.lab.knowledge_bases.test_suites.indexes.splitter]
    kind = "test_suite_md"   # same behavior as pais-dev in v0.3
    budget_tokens = 400
```

To get the old default behavior with no config edit, pass `--splitter test_suite_md` on the CLI.

## Migration path

1. Install v0.4: `pip install --upgrade "git+https://github.com/dshahnaz/pais-sdk-cli.git@v0.4.0"`
2. Verify: `pais --version` should print `pais 0.4.0`. Also: `pais-dev` will print a redirect message and exit 1 — confirm you don't have it in any scripts (or update them).
3. (Recommended) declare your KB+index in TOML so you can use aliases:
   ```bash
   pais config init                      # or edit ~/.pais/config.toml directly
   $EDITOR ~/.pais/config.toml           # add [profiles.lab.knowledge_bases.*] blocks
   pais --profile lab kb ensure          # creates anything missing on the server
   ```
4. Update scripts: replace `pais-dev ingest-suites` with `pais ingest <alias>:<alias>`.

## Backwards compatibility

- All v0.3 commands under `pais` (kb, index, agent, mcp, models, mock, config) keep working unchanged.
- All v0.3 commands continue to accept UUIDs everywhere they did before; aliases are an additive convenience.
- The Python SDK API (`PaisClient`, `Settings`, etc.) is unchanged.
- `pais.dev.split_suite.split_suite()` and friends still importable as internal helpers (used by the new `pais.ingest.splitters.test_suite_md`).
