# Synthea direct HAPI database loader

This standalone Rust program loads Synthea R4 bundles into the PostgreSQL schema used by HAPI FHIR JPA 8.4. It avoids FHIR transaction processing during the initial load:

1. Scan Synthea JSON, JSON.gz, and NDJSON files twice. The first pass builds a reference index; the second flattens bundles and rewrites `urn:uuid:` and `Type?identifier=...` references to stable `Type/id` references.
2. Stream normalized resources into a PostgreSQL temporary table with `COPY`.
3. Insert `HFJ_RESOURCE` and `HFJ_RES_VER` rows in one transaction using HAPI's own sequences.
4. Start HAPI's `$reindex` job and watch its Batch2 status in PostgreSQL. Reindexing is required because HAPI search parameter extraction is version-specific and must remain owned by HAPI.

The loader validates the 8.4 schema before writing and defaults to `initialize` mode, which refuses to modify a non-empty repository.

## Run locally

```bash
export PGHOST=127.0.0.1 PGPORT=5432 PGUSER=hapi PGPASSWORD=... PGDATABASE=hapi
cargo run --release -- \
  --input /path/to/synthea/output \
  --fhir-base-url http://127.0.0.1:8080/fhir
```

Parse and resolve references without touching the database:

```bash
cargo run --release -- --input /path/to/synthea/output --dry-run
```

Use `--mode append` to insert only logical IDs that do not already exist. Existing resources are never overwritten, because doing that outside HAPI would require coordinated history and search-index updates.

## Operational constraints

- This is intentionally pinned to HAPI FHIR JPA 8.4 and PostgreSQL. HAPI's schema is an internal implementation detail, so upgrades must be tested and may require loader changes.
- Run it as a controlled startup/maintenance job. Do not allow concurrent FHIR writes during the direct-load transaction.
- Direct loading bypasses request validation, interceptors, subscriptions, and audit hooks. It is intended only for trusted, generated Synthea seed data.
- Do not use `--skip-reindex` for a ready-to-query environment. Without reindexing, direct `Type/id` reads and type counts work, but searches using `identifier`, `patient`, `code`, dates, and other parameters are incomplete.
- The default partition is the unpartitioned HAPI repository (`PARTITION_ID IS NULL`). Database partition mode is not supported.

