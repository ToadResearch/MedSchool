"""Convert the packaged fhir-env train/dev questions without importing its runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


SOURCE_URL = "https://github.com/ToadResearch/fhir-env"
SOURCE_PATH = Path("environments/fhir_workflows/fhir_workflows/training_data")


def convert_task(row: dict, revision: str) -> dict:
    gold = row["gold"]
    operations = ["read"]
    for operation, key in (("create", "creates"), ("update", "updates"), ("delete", "deletes")):
        if gold.get(key):
            operations.append(operation)
    # Keep heterogeneous upstream objects as JSON strings: Arrow otherwise merges
    # answer keys across families or rejects mixed scalar/object mutation values.
    metadata = {k: v for k, v in row.items() if k not in {"id", "prompt", "gold", "reference_steps"}}
    return {
        "id": row["id"],
        "type": operations,
        "meta": {
            "dataset": "fhir_env",
            "source": SOURCE_URL,
            "source_revision": revision,
            "split": row["split"],
            "family": row["family"],
            "patient_id": row["patient_id"],
            "shard": row["shard"],
            "upstream_metadata": json.dumps(metadata, ensure_ascii=False, sort_keys=True),
        },
        "input": {"task": row["prompt"], "context": ""},
        "output": {
            "answer": json.dumps(gold["answer"], ensure_ascii=False, sort_keys=True),
            "upstream_gold": json.dumps(gold, ensure_ascii=False, sort_keys=True),
            "reference_steps": json.dumps(row["reference_steps"], ensure_ascii=False, sort_keys=True),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_checkout", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "tasks")
    args = parser.parse_args()
    revision = subprocess.check_output(
        ["git", "-C", str(args.source_checkout), "rev-parse", "HEAD"], text=True
    ).strip()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"source": SOURCE_URL, "source_revision": revision, "splits": {}}
    for split in ("train", "dev"):
        source = args.source_checkout / SOURCE_PATH / split / "tasks.jsonl"
        destination = args.output_dir / f"fhir_env_{split}.json"
        ids = set()
        families = set()
        with source.open() as rows, destination.open("w") as output:
            output.write("[\n")
            for index, line in enumerate(rows):
                row = json.loads(line)
                if row["id"] in ids or row["split"] != split:
                    raise ValueError(f"Duplicate ID or wrong split: {row['id']}")
                ids.add(row["id"])
                families.add(row["family"])
                if index:
                    output.write(",\n")
                output.write(json.dumps(convert_task(row, revision), ensure_ascii=False))
            output.write("\n]\n")
        manifest["splits"][split] = {
            "source_file": str(SOURCE_PATH / split / "tasks.jsonl"),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "task_file": destination.name,
            "task_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "tasks": len(ids),
            "families": sorted(families),
        }
        print(f"Converted {len(ids)} {split} tasks -> {destination}")
    (args.output_dir / "fhir_env_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
