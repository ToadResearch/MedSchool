# MedSchool

MedSchool benchmarks clinical agents against a FHIR R4 EHR, with a persistent
Docker terminal sandbox for each task. Synthetic Hospital v1.3 supplies the chart
data; the benchmark runner evaluates agents and saves their trajectories and rewards.

## Start the Docker environment

Requires Docker with Compose. From this directory:

```bash
cp .env.example .env  # first setup only; retain your existing .env
./startup.sh --data --mcp
```

The stack includes PostgreSQL, HAPI FHIR, the Middleman API/proxy, and Alpine
sandboxes. `--data` downloads the pinned Synthetic Hospital training split,
converts it to FHIR, loads it directly into PostgreSQL, and waits for search
reindexing. `--mcp` adds the MCP tool server; the benchmark runner uses Middleman
and does not require MCP.

For subsequent runs, use `./startup.sh` or `./startup.sh --mcp` to reuse the data.
After removing old services, use `./startup.sh --remove-orphans` to remove their
leftover containers while keeping data volumes.
The seed jobs are opt-in and do not run during ordinary `docker compose up`.
See the [Synthetic Hospital import guide](docker/synthetic_hospital/README.md)
for cohort selection, mappings, checksums, and migration from an existing database,
and the [direct loader guide](docker/direct_loader/README.md) for loader options.

## Run benchmarks

```bash
cd environment
uv sync --frozen
uv run eval.py \
  --model gpt-oss-120b \
  --api-key-var CEREBRAS_API_KEY \
  --base-url https://api.cerebras.ai/v1 \
  --task-filename synthetic_hospital_counts
```

Set the chosen provider's API key in `.env`. Any OpenAI-compatible endpoint can
be selected with these flags. Results go to
`environment/outputs/<model>/<timestamp>/results.json`.

The checked-in count benchmark matches the pristine training seed (800 patients,
17,304 resources). Regenerate counts for other cohorts. `toy_tasks` provides small
terminal exercises. Custom task files can be passed with `--task-filepath`.
See [benchmark configuration and tools](environment/README.md).

The Synthetic Hospital integration imports full charts; it does not implement
upstream's protected diagnosis/imaging benchmark or its four scorers. See the
import guide before using those evaluation protocols.

## Tests

Offline converter and loader checks:

```bash
python3 -m unittest discover -s docker/synthetic_hospital -v
cargo test --locked --manifest-path docker/direct_loader/Cargo.toml
cd environment
uv run python -m unittest discover -s tests -v
```

For live Docker checks, start the stack and validator, then run the retained
FHIR CRUD/query and validator scripts (requires `curl` and `jq`):

```bash
docker compose up -d --build validator validator-prewarm
bash docker/fhir_server/scripts/test_fhir_queries.sh
bash docker/val_server/scripts/test_val_server.sh
```

The FHIR smoke test creates and deletes test resources. Run count benchmarks on
a pristine seed, without concurrent CRUD tests or other writers. Interactive
FHIR and sandbox checks are also available in [environment/repls](environment/repls/README.md).

## Stop

- `./shutdown.sh`: stop services and clean up sandbox sessions, keeping data.
- `./shutdown.sh --down`: also remove service containers, keeping data volumes.
- `./shutdown.sh --purge`: remove containers, images, and data volumes after confirmation.

`./startup.sh --reset --data` also deletes all stack volumes before reseeding.
