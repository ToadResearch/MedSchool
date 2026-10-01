# Synthetic Hospital seed data

MedSchool imports the released [Synthetic Hospital](https://github.com/sparkcpark/synthetic_hospital)
v1.3 chart data into its existing HAPI FHIR R4 server. The upstream simulator and
its PostgreSQL schema are not substituted for HAPI.

The downloader pins commit `911f34c4ac65a508543c4b3b90c373a0cd16534d` and verifies
`benchmark_v1.3.db` with SHA-256
`8fb8eb114508f272588b6121ca78cff53a6fc2e5f47d7aa01bfbb5bcef4e6ddb`.
No API key or generation step is needed. Source data is cached in a Docker volume;
large databases and exports are not checked into MedSchool.

## Start

```bash
./startup.sh --data --mcp
```

The converter runs before the existing Rust loader, which loads PostgreSQL and
waits for HAPI search reindexing. Later starts can use `./startup.sh --mcp`.
The one-shot services use the `seed` Compose profile so ordinary `docker compose up`
does not import data. Use `startup.sh --data` to sequence import and MCP startup.
`--synthea` and `--save-synthea` are retired; the new download cache is retained.

An existing Synthea database is rejected, not silently combined with the new data.
Back up anything you need first. To explicitly discard **all stack volumes** and
replace the old dataset, run `./startup.sh --reset --data --mcp`. This migration
never resets your running database automatically. Pause other clients that can
write FHIR resources during seed loading.

The default split is `train` (800 patients). Set `SYNTHETIC_HOSPITAL_SPLIT` in `.env`
to `public`, `heldout`, or `all` for other uses. Do not train on held-out data.
Use a fresh database when changing cohorts; append mode does not remove old records
or update existing resources. The cohort guard refuses existing patients absent
from the input. Repeated imports of the same pristine seed are idempotent and
rerun reindexing, allowing recovery from a previous indexing failure.

## What is imported

- `Patient`: original patient ID as an identifier, sex mapped to FHIR gender.
  The release has no names or birth dates; none are fabricated.
- `Encounter`: visit type/class, date, chief complaint, patient and attending reference.
- `Practitioner`: the documented attending display name, scoped per encounter because
  equal names do not establish provider identity.
- `DocumentReference`: each original clinical note as a base64 UTF-8 text attachment,
  plus each complete original patient profile as an `application/json` attachment.
  Profile attachments preserve age, family/surgical/social history and other fields.
- `Condition`, `MedicationStatement`, `AllergyIntolerance`: documented profile history,
  home medications and positive allergy statements, retaining source text. Negative
  allergy statements stay in the profile. No inferred codes, dates, lab values,
  medication orders, or clinical statuses are invented.

This is a chart import for MedSchool's existing CRUD environment, **not** an
implementation of upstream's four benchmark scorers or protected RL environment.
Ground-truth answers, graph-derived diagnoses and relevance judgments are not
exported. Complete chart notes can still contain diagnostic information, including
assessment/plan when present. For faithful upstream diagnosis/imaging evaluation,
use their protected `/env` workflow or implement equivalent task-specific filtering;
this full-chart FHIR endpoint is not that evaluation boundary.

See the upstream [data card](https://github.com/sparkcpark/synthetic_hospital/blob/911f34c4ac65a508543c4b3b90c373a0cd16534d/DATA_CARD.md)
for provenance, limitations, licensing and splits, and their
[citation](https://github.com/sparkcpark/synthetic_hospital/blob/911f34c4ac65a508543c4b3b90c373a0cd16534d/CITATION.cff)
when using this dataset in research.

## Local export and checks

Python 3.11+; standard library only:

```bash
python3 docker/synthetic_hospital/export_fhir.py --download \
  --source /tmp/synthetic-hospital/benchmark_v1.3.db \
  --output /tmp/synthetic-hospital/fhir --split train \
  --tasks-dir environment/tasks
python3 -m unittest discover -s docker/synthetic_hospital -v
cargo run --locked --manifest-path docker/direct_loader/Cargo.toml -- \
  --input /tmp/synthetic-hospital/fhir --dry-run
```

`resources.ndjson` is published atomically. `manifest.txt` records revision, source
checksum, split and counts; keep it outside the FHIR JSON format. `--source` also
accepts an offline database without downloading (its actual checksum is recorded).

The checked-in `environment/tasks/synthetic_hospital_counts.json` matches the
pinned training export (17,304 resources, 800 patients, 3,539 encounters).
Regenerate it with `--tasks-dir` for a different split. Counts assume a pristine
seed with no subsequent CRUD changes. Old `counts.json` and `names.json` are
legacy Synthea fixtures and must not be used with this dataset.
