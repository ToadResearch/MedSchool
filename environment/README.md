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

## Your first benchmark run

Start with [`tasks/starter.json`](tasks/starter.json): 25 read-only questions
covering resource counts, empty resource types, totals, and count differences.
The first question asks the agent to count Patient resources, with an expected
answer of 800 for the pristine Synthetic Hospital training split. Expected
answers are used by the scorer and are not included in the agent's prompt.
Use `uv run eval.py -t starter` to run the full starter list.

1. Open Docker Desktop and wait until its engine is running. From the repository
   root, run `./startup.sh --data` to import the training seed. If that seed is
   already loaded, use `./startup.sh` instead. These commands preserve existing
   data; an incompatible old cohort is rejected. Do not use `--reset` unless you
   intend to delete all stack volumes.
2. Check the dataset with
   `bash docker/fhir_server/scripts/query_hapi.sh`. Patient should be 800 and
   Encounter should be 3539 for the pristine training split.
3. From `environment/`, run `uv sync --frozen` to install the locked dependencies.
4. Select your provider's model, endpoint, and API-key variable. The following
   example uses the runner's Cerebras defaults and expects `CEREBRAS_API_KEY` in
   your root `.env` or shell environment:

   ```bash
   uv run eval.py -t starter --requested 1
   ```

   For a different OpenAI-compatible provider, supply all three settings:

   ```bash
   uv run eval.py -t starter --requested 1 \
     -m YOUR_MODEL -b YOUR_API_BASE_URL -k YOUR_KEY_VARIABLE
   ```

5. The runner creates one sandbox, lets the agent query FHIR/use its tools, scores
   the final answer, and stops the sandbox. A successful answer produces:

   ```text
   Average reward: 1.0000
   Samples with full reward (1.0): 1/1
   Results saved to outputs/<model>/<timestamp>/results.json
   ```

   Open the exact results path printed by the runner to inspect the completion
   and reward. A zero score means the expected answer was absent from the final
   response; inspect the trajectory for tool errors or an incorrect answer.
6. Run all seven resource-count questions with
   `uv run eval.py -t synthetic_hospital_counts` (plus your provider flags).
   `--requested` caps the number of examples, not the model's token budget.
7. From the repository root, run `./shutdown.sh` when finished to stop services
   while keeping the imported data.

This starter verifies the evaluation plumbing and simple FHIR retrieval. A 1/1
score is not a measure of general clinical ability. The current answer scorer
checks whether the expected value appears in the response. Model calls use your
provider account; no live model calls are made by the offline regression tests.

## Tasks and scoring

`fhir_env_train` and `fhir_env_dev` contain 19,530 and 2,186 converted questions
from ToadResearch/fhir-env across 37 workflow families. See the
[imported benchmark guide](tasks/fhir_env_README.md) for provenance, conversion,
and fixture requirements. These questions require upstream's expanded FHIR
charts; the current Synthetic Hospital seed does not contain those fixtures.

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

For scalar answers, the rubric awards 1 when the normalized expected answer
occurs in the final response, otherwise 0. For JSON objects/lists, it compares
the structured fields, accepting the upstream `answer`/`evidence` envelope.
This is an answer check; it does not verify evidence or chart mutations and does
not implement Synthetic Hospital's upstream clinical scorers.

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
