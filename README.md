# MedSchool 🩺 🤗 

> Imagine if all major coding benchmarks were multiple-choice QA

That's the current state of affairs for medical/clinical benchmarking, outside of maybe the new conversational HealthBench that has the big lab spotlight on it right now.

Medical/clinical data is semi-verifiable: it's a mix of objective and subjective statements, free-text and discrete fields, etc. EHRs store all this data and serve as ground truth repositories for documented clinical realities, and because they are highly structured, they are implicitly verifiable. But to the best of our knowledge, there have been no public works applying RLVR to agentic EHR tasks. We aim to change that!

We think that EHRs are the gateway to most clinical tasks. From what we've seen with programming, we believe the best way to develop clinical intelligence is by giving models the ability to take action and learn from experience within EHR environments. Some more about *the plan* can be found [here](https://x.com/mkieffer1107/status/1958644405411225788). We might spruce this up in the future and turn it into a blog post.

---
### Want to help?

![Under construction](assets/under_construction.gif)

> This project is actively under development and there are many known bugs!


Right now we only have basic MCP support, and are beginning to work on the environment itself. The two biggest challenges to solve:

1) Figure out the minimal MCP toolset to best handle EHR tasks. Many to choose from [here](https://zitniklab.hms.harvard.edu/TxAgent/) and [here](https://github.com/snap-stanford/Biomni).
2) Figure out how to generate enviroment tasks automatically. It should be *relatively* easy to generate single-hop tasks.

If you're interested in clinical intelligence, developing realistic health/medical benchmarks, or creating an open-source copilot for doctors, consider helping out!

---

### Current tools available:

- **FHIR:**
  - **fhir_query**: read FHIR records

- **Code Execution:**
  - **python_exec**: execute python scripts in a sandbox
  - **shell_exec**: execute shell scripts and commands in a sandbox

- **Terminology:**
  - **code_lookup**: get display name and synonyms for a given code (e.g., ICD-10, CPT/HCPCS, SNOMED, LOINC, RxNorm)

- **OpenFDA:**
  - **openfda_label**: fetch FDA drug label (SPL) sections like indications, warnings, contraindications, dosage
  - **openfda_adverse_events**: query FAERS adverse event reports (serious %, top reactions, sample cases, outcome filters)
  - **openfda_recalls**: search FDA drug enforcement reports (recalls) with classification, status, and reason
  - **openfda_drug_shortages**: fetch FDA drug shortages (current/archived) with status, last-updated, and reason


Note: It might be better to migrate most work into a terminal environment because FHIR records are very large json objects that quickly fill context windows. For example, if you're doing payment analysis over a patient record, it might be best to pipe FHIR query results directly into a python process, rather than wasting context to copy and paste it in. This is especially a problem when running models locally. Notepads (like in Claude plays Pokemon, [here](https://x.com/omarsar0/status/1961073840706203804), or even [here](https://x.com/EyubogluSabri/status/1932106746446905552)) to store additional context could help as long as no data is leaked between patients. Working inside a terminal would also let us use the [CLI FHIR validator](https://github.com/hapifhir/org.hl7.fhir.validator-wrapper), instead of pinging the server.

---

### Basic tool-calling demo:

Copy the example environment file to `.env` and update the api keys inside:

```bash
cp .env.example .env
```

Start all Docker services and download/load synthetic patient data into the server:

```bash
./startup.sh --synthea
```
Connect the MCP server to the client of your choice:

**Option A — Update your local client’s `mcp.json`:**

```json
{
  "mcpServers": {
    "medschool-mcp": {
      "url": "http://127.0.0.1:3000/mcp_server/mcp"
    }
  }
}
```

**Option B — Use the interactive API REPL:**

Run `api_repl.py` with your provider of choice.

```sh
# Cerebras
python api_repl.py \
  -m gpt-oss-120b \
  -k CEREBRAS_API_KEY \
  -b https://api.cerebras.ai/v1
```

```sh
# Groq
python api_repl.py \
  -m openai/gpt-oss-120b \
  -k GROQ_API_KEY \
  -b https://api.groq.com/openai/v1
```

```sh
# OpenRouter
python api_repl.py \
  -m openai/gpt-oss-120b \
  -k OPENROUTER_API_KEY \
  -b https://openrouter.ai/api/v1
```
---

### How to remove everything:

To stop all services and completely delete all containers, data volumes, and associated images, run the purge command:
```sh
./shutdown.sh --purge
```

