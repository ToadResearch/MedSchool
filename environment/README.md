### How to run:

We use the [Verifiers library](https://github.com/willccbb/verifiers). We're still in early development, so bear with us as we improve the workflow :)

First, set up the environment:

```bash
uv venv --python 3.12 --seed
source .venv/bin/activate
uv sync
```

Make sure the HAPI server is running by executing the following command inside the base project directory. If this is your first time running it, please be sure to add the `--synthea` flag to generate data. Omit this flag on subsequent runs, as the current setup will regenerate the data.

   ```bash
   ./startup.sh [--synthea]
   ```

To run evals, come back to this directory and run any of the following


```bash
# Cerebras
python eval.py \
  -m gpt-oss-120b \
  -k CEREBRAS_API_KEY \
  -b https://api.cerebras.ai/v1 \
  -t single
```

```bash
# Groq
python eval.py \
  -m openai/gpt-oss-120b \
  -k GROQ_API_KEY \
  -b https://api.groq.com/openai/v1
  -t basic
```
```bash
# OpenRouter
python eval.py \
  -m openai/gpt-oss-120b \
  -k OPENROUTER_API_KEY \
  -b https://openrouter.ai/api/v1
  -t basic
```

Details about the CLI args are available below. In general, any OpenAI-compatible API endpoint should work.

To shutdown the server and stop all services run the following command in the base project directory. The `--purge` flag will stop all services and completely delete all containers, data volumes, and associated images.

   ```bash
   ./shutdown.sh [--purge]
   ```

---

**CLI Args**

The `eval.py` script supports various options:
- `-m, --model`: Model alias or provider model name (default: gpt-oss-120b)
- `-k, --api-key-var`: Environment variable for API key (default: CEREBRAS_API_KEY)
- `-b, --base-url`: Base URL for the API (default: https://api.cerebras.ai/v1)
- `-t, --task-filename`: Task name to load from ./tasks/ directory (appends .json automatically)
- `--task-filepath`: Full path to the task JSON file
- `--requested`: Max number of examples to evaluate (default: 1000)

**Note**: Either `--task-filepath` or `--task-filename` must be provided.

New tasks can be added as `<task_name>.json` to the [tasks directory](tasks), and may be called using the flag `-t <task_name>`.

---

### Current tools available:

- **FHIR:**
  - **fhir_post**: create a FHIR resource
  - **fhir_get**: read a FHIR resource
  - **fhir_update**: update a FHIR resource
  - **fhir_delete**: delete a FHIR resource
  - **fhir_validate**: validate a FHIR resource


- **Code Sandbox**
  - **terminal_command**: execute a terminal command in a persistent sandbox

- **Terminology:**
  - **code_lookup**: get display name and synonyms for a given code (e.g., ICD-10, CPT/HCPCS, SNOMED, LOINC, RxNorm)

- **OpenFDA:**
  - **openfda_label**: fetch FDA drug label (SPL) sections like indications, warnings, contraindications, dosage
  - **openfda_adverse_events**: query FAERS adverse event reports (serious %, top reactions, sample cases, outcome filters)
  - **openfda_recalls**: search FDA drug enforcement reports (recalls) with classification, status, and reason
  - **openfda_drug_shortages**: fetch FDA drug shortages (current/archived) with status, last-updated, and reason


Note: We've migrated from ephemeral code-execution sandboxes to a full terminal environment because FHIR records are very large json objects that quickly fill context windows. For example, if the model is doing payment analysis over a patient record, it might be best to pipe FHIR query results directly into a python process, rather than wasting context to copy and paste them into a python file it's writing. This is especially a problem when running models locally. 

So, we'll have to add a way to pipe tool calls or potentially instruct the model about how to make FHIR queries within code using the correct addresses (e.g., to perform many FHIR queries with a single tool call). We're a little wary of this approach right now, and think it might be better to perhaps return line/char counts for FHIR get requests. This way the model can decide to personally inspect it, or save it as a json file within the sandbox to work with. A REPL for FHIR tool calls might help with this.

Notepads (like in Claude plays Pokemon, [here](https://x.com/omarsar0/status/1961073840706203804), or even [here](https://x.com/EyubogluSabri/status/1932106746446905552)) to store additional context could help as long as no data is leaked between patients. Working inside a terminal would let the model just write text files, and also let us use the [CLI FHIR validator](https://github.com/hapifhir/org.hl7.fhir.validator-wrapper), instead of pinging the server, if we wanted.


