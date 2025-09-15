### MCP tool-calling demo:

This is a demo of the previous system we used. We no longer use an MCP server, but wanted to keep this here for a while since it's nice to play with. 
#### Current tools available:

- **FHIR:**
  - **fhir_query**: read FHIR records
  - **fhir_validate**: validate a FHIR resource

- **Code execution:**
  - **shell_exec**: ephemeral sandbox with access to python and shell commands. the full environment has a sandbox that persists throughout the duration of a task

- **Terminology:**
  - **code_lookup**: get display name and synonyms for a given code (e.g., ICD-10, CPT/HCPCS, SNOMED, LOINC, RxNorm)

- **OpenFDA:**
  - **openfda_label**: fetch FDA drug label (SPL) sections like indications, warnings, contraindications, dosage
  - **openfda_adverse_events**: query FAERS adverse event reports (serious %, top reactions, sample cases, outcome filters)
  - **openfda_recalls**: search FDA drug enforcement reports (recalls) with classification, status, and reason
  - **openfda_drug_shortages**: fetch FDA drug shortages (current/archived) with status, last-updated, and reason

---

#### How to run:

Go back to the root directory and make sure everything is running first, if you haven't already. 

1. Copy the example environment file to `.env` and update the API keys inside for any LLMs you'd like to use:

   ```bash
   cp .env.example .env
   ```

2. Start all Docker services and generate/load synthetic patient data into the server. Make sure to use the `--mcp` flag to start the MCP server. Only use the `--synthea` flag the first time the server is started to generate data.

   ```bash
   ./startup.sh --mcp [--synthea]
   ```


Now it's safe to come back to this directory.

While you wait for everything to start, setup the venv and install packages

```bash
uv venv --python 3.12 --seed
source .venv/bin/activate
uv sync
```

Connect the MCP server to the client of your choice:

**Option A — Use the interactive API REPL:**

Run `repl.py` with your provider of choice. Any OpenAI-compatible API endpoint should work.

```sh
# Cerebras
python repl.py \
  -m gpt-oss-120b \
  -k CEREBRAS_API_KEY \
  -b https://api.cerebras.ai/v1
```

```sh
# Groq
python repl.py \
  -m openai/gpt-oss-120b \
  -k GROQ_API_KEY \
  -b https://api.groq.com/openai/v1
```

```sh
# OpenRouter
python repl.py \
  -m openai/gpt-oss-120b \
  -k OPENROUTER_API_KEY \
  -b https://openrouter.ai/api/v1
```


**Option B — Update your local client’s `mcp.json`:**

```json
{
  "mcpServers": {
    "medschool-mcp": {
      "url": "http://127.0.0.1:3000/mcp_server/mcp"
    }
  }
}
```

---

### How to remove everything:

To stop all services and completely delete all containers, data volumes, and associated images, run the purge command:
```sh
./shutdown.sh --purge
```

