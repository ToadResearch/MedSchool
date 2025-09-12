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


Note: It might be better to migrate most work into a terminal environment because FHIR records are very large json objects that quickly fill context windows. For example, if you're doing payment analysis over a patient record, it might be best to pipe FHIR query results directly into a python process, rather than wasting context to copy and paste it in. This is especially a problem when running models locally. Notepads (like in Claude plays Pokemon, [here](https://x.com/omarsar0/status/1961073840706203804), or even [here](https://x.com/EyubogluSabri/status/1932106746446905552)) to store additional context could help as long as no data is leaked between patients. Working inside a terminal would also let us use the [CLI FHIR validator](https://github.com/hapifhir/org.hl7.fhir.validator-wrapper), instead of pinging the server.

---

### How to run:

First, set up the environment:

```bash
uv venv --python 3.10 --seed
source .venv/bin/activate
uv sync
```

Make sure the HAPI server is running.


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