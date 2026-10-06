# Imported fhir-env questions

These are the complete packaged `training_data` questions from
[ToadResearch/fhir-env](https://github.com/ToadResearch/fhir-env/tree/3f0b91894e1063c3e19ec188baf4d122afa3aac4),
commit `3f0b91894e1063c3e19ec188baf4d122afa3aac4`:

| Task file | Questions | Workflow families |
| --- | ---: | ---: |
| `fhir_env_train.json` | 19,530 | 37 |
| `fhir_env_dev.json` | 2,186 | 37 |

The upstream infrastructure pilot is a separate corpus and is not included.
Public/heldout splits and optional discovery variants are not packaged in these
source task files. The original train/dev partition, IDs, prompts, patient
references, dates, and answers are preserved. These are converted existing
questions, with no model-generated questions or answers.

## Format

Each task uses MedSchool's `id`, `type`, `meta`, `input`, and `output` fields.
`input.task` is the exact upstream prompt; `input.context` is empty.
`type` includes `read` and any requested `create`, `update`, or `delete`
operations, inferred from the gold mutations rather than the HTTP method of a
transaction Bundle.

`output.answer` is a JSON-encoded string containing `gold.answer`.
The answer scorer parses structured answers and compares their fields, ignoring
JSON key order and whitespace. It accepts the upstream `answer`/`evidence`
envelope or the answer object alone, including fenced JSON. Scalar tasks retain
the existing normalized substring check.

The full upstream gold object and reference calls are preserved as JSON strings
in `output.upstream_gold` and `output.reference_steps`. Remaining upstream fields
are preserved in `meta.upstream_metadata`, also as a JSON string. This avoids
Arrow merging different families' answer fields or rejecting mixed mutation
value types. Only the question and context enter the agent prompt; gold,
reference calls, and metadata do not.

`fhir_env_manifest.json` records the source revision, paths, SHA-256 hashes,
counts, and families for reproducibility. Retained upstream third-party notices
and the Synthetic Hospital source license are in `fhir_env_THIRD_PARTY_NOTICES.md`
and `fhir_env_SYNTHETIC_HOSPITAL_LICENSE`.

## Evaluation requirements

**The current MedSchool Synthetic Hospital seed cannot answer these questions.**
They require the expanded upstream v0.3.0 FHIR charts in the matching
`training_data/<split>/shards/*.ndjson`, including their exact resource IDs,
identifiers, and generated records. This conversion does not import those charts
or change the Docker seed. `meta.shard` identifies each task's required shard;
some tasks also specify omissions in `meta.upstream_metadata`.

MedSchool currently scores only the final answer. It does not enforce upstream's
evidence retrieval, allowed mutations, ETags, transactions, or preserved fields.
The gold checks are retained for a future workflow scorer; a correct answer
alone does not demonstrate successful CRUD execution. Write tools are disabled
by default. CRUD evaluation additionally requires enabled tools and isolation
and restoration of each task's chart state, since the current sandboxes share
one FHIR database.

After provisioning the matching charts and task state, select a split from
`environment/`:

```bash
uv run eval.py -t fhir_env_dev --requested 10
uv run eval.py -t fhir_env_train
```

The runner defaults to at most 1,000 questions; use `--requested 2186` for all
dev questions or `--requested 19530` for all train questions. Supply the usual
provider flags as needed.

## Reproduce the conversion

From the repository root, with a checkout at the pinned revision:

```bash
git clone https://github.com/ToadResearch/fhir-env.git /tmp/fhir-env
git -C /tmp/fhir-env checkout 3f0b91894e1063c3e19ec188baf4d122afa3aac4
python3 environment/scripts/import_fhir_env_tasks.py /tmp/fhir-env
cd environment
uv run python -m unittest discover -s tests -v
```

The importer uses only the Python standard library and Git. It retains all
upstream task fields and never executes upstream code or makes model calls.
