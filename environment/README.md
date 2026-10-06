# Benchmark environment

The Verifiers-based runner evaluates an agent through FHIR tools and a dedicated
Docker terminal sandbox for each task. It scores final answers and saves
trajectories and rewards as JSON.

## Setup and evaluation

Start Docker from the repository root with `./startup.sh --data` on the first run,
then `./startup.sh` on subsequent runs. See the
[Synthetic Hospital import guide](../docker/synthetic_hospital/README.md) before
replacing an existing database or changing cohorts.

From this directory (Python 3.12+ and `uv`):

```bash
uv sync --frozen
uv run eval.py \
  -m gpt-oss-120b \
  -k CEREBRAS_API_KEY \
  -b https://api.cerebras.ai/v1 \
  -t synthetic_hospital_counts
```

Set your provider's API key in the repository `.env`. The runner accepts any
OpenAI-compatible endpoint. Outputs are written to
`outputs/<model>/<timestamp>/results.json`, alongside console reward summaries.

| Option | Purpose |
| --- | --- |
| `-m`, `--model` | Provider model name (default: `gpt-oss-120b`) |
| `-k`, `--api-key-var` | API key environment variable (default: `CEREBRAS_API_KEY`) |
| `-b`, `--base-url` | API endpoint (default: `https://api.cerebras.ai/v1`) |
| `-t`, `--task-filename` | Name under `tasks/`, without `.json` |
| `--task-filepath` | Custom task JSON path |
| `--requested` | Maximum examples to evaluate (default: 1000) |

Supply a task filename or path; the filename takes precedence when both are given.
Run commands from this directory so the task and config paths resolve correctly.
Concurrency is capped by `sandbox.max_concurrent_sessions` in
`configs/sandbox.yaml` and the number of examples.

## Tasks and scoring

`synthetic_hospital_counts` matches the pinned pristine training split (800
patients, 17,304 resources). Regenerate it with the converter's `--tasks-dir`
option for other splits. Concurrent writes change counts, so benchmark a pristine
seed. `toy_tasks` contains small terminal exercises and is the default for
`load_environment()`.

Custom tasks use this format:

```json
[
  {
    "id": "patient-count",
    "type": ["read"],
    "input": {"task": "How many patients are in the FHIR database?", "context": ""},
    "output": {"answer": 800}
  }
]
```

The current rubric awards 1 when the normalized expected answer occurs in the
final response, otherwise 0. This is a basic answer check; it does not implement
Synthetic Hospital's upstream clinical scorers.

## Tools and checks

`configs/tools.yaml` controls enabled tools and timeouts; `configs/system-prompt.txt`
sets the agent instructions. Available tools cover FHIR reads and CRUD,
validation, terminal commands, terminology lookup, and OpenFDA queries.
FHIR reads save the full JSON in the sandbox and return compact metadata.
CRUD tools are disabled by default.

Offline benchmark regression checks:

```bash
uv run python -m unittest discover -s tests -v
```

Use the [interactive REPLs](repls/README.md) for FHIR queries and session/tool
checks against Docker. The repository README lists the live FHIR and validator
test scripts and shutdown commands.
