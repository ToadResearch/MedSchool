"""Convert pinned Synthetic Hospital v1.3 chart data to FHIR R4 (stdlib only)."""
import argparse
import base64
from collections import Counter
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import urllib.request

REVISION = "911f34c4ac65a508543c4b3b90c373a0cd16534d"
SHA256 = "8fb8eb114508f272588b6121ca78cff53a6fc2e5f47d7aa01bfbb5bcef4e6ddb"
SOURCE = "https://github.com/sparkcpark/synthetic_hospital"
URL = f"https://raw.githubusercontent.com/sparkcpark/synthetic_hospital/{REVISION}/benchmark_v1.3.db"
SYSTEM = SOURCE + "/patient-id"
CLASSES = {"outpatient": "AMB", "ed": "EMER", "inpatient": "IMP", "icu": "IMP", "telehealth": "VR", "procedure": "AMB", "follow_up": "AMB"}


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and checksum(path) == SHA256:
        return
    temporary = path.with_suffix(".download")
    try:
        with urllib.request.urlopen(URL, timeout=120) as response, temporary.open("wb") as out:
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
        if checksum(temporary) != SHA256:
            raise ValueError("Downloaded dataset checksum differs from the pinned release")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def resource(kind, key, **fields):
    return {"resourceType": kind, "id": f"sh-{key}", **fields}


def document(key, patient, text, title, encounter=None, content_type="text/plain"):
    result = resource("DocumentReference", key, status="current", subject=patient,
                      type={"text": title}, content=[{"attachment": {
                          "contentType": content_type, "title": title,
                          "data": base64.b64encode(text.encode()).decode()}}])
    if encounter:
        result["context"] = {"encounter": [encounter]}
    return result


def chart_resources(db, split):
    # Split labels select patients only; reference answers/graph diagnoses never become chart data.
    patients = db.execute("""SELECT p.* FROM longitudinal_patients p WHERE EXISTS
        (SELECT 1 FROM benchmark_ground_truth g WHERE g.patient_id=p.patient_id
         AND (?='all' OR g.split=?)) ORDER BY p.patient_id""", (split, split))
    for row in patients:
        pid = row["patient_id"]
        profile = json.loads(row["profile"])
        patient = {"reference": f"Patient/sh-p{pid}"}
        yield resource("Patient", f"p{pid}", identifier=[{"system": SYSTEM, "value": str(pid)}],
                       gender={"M": "male", "F": "female"}.get(row["sex"], "unknown"))
        # The release has no patient names or DOBs. Keep original age and all profile fields
        # in a JSON attachment rather than fabricating names, dates or coded clinical facts.
        yield document(f"profile-p{pid}", patient, json.dumps(profile, ensure_ascii=False, sort_keys=True),
                       "Synthetic Hospital patient profile", content_type="application/json")
        for i, condition in enumerate(profile.get("chronic_conditions", [])):
            yield resource("Condition", f"condition-p{pid}-{i}", subject=patient,
                           code={"text": condition})
        for i, medication in enumerate(profile.get("home_medications", [])):
            yield resource("MedicationStatement", f"medication-p{pid}-{i}", subject=patient,
                           status="unknown", medicationCodeableConcept={"text": medication["name"]},
                           **({"dosage": [{"text": medication["dose"]}]} if medication.get("dose") else {}))
        for i, allergy in enumerate(profile.get("allergies", [])):
            # Negative allergy statements remain verbatim in the profile, not positive allergies.
            if allergy.strip().lower() in {"nkda", "no known drug allergies", "no known allergies", "none"}:
                continue
            yield resource("AllergyIntolerance", f"allergy-p{pid}-{i}", patient=patient, code={"text": allergy})
        for visit in db.execute("SELECT * FROM longitudinal_encounters WHERE patient_id=? ORDER BY encounter_order, encounter_id", (pid,)):
            eid = visit["encounter_id"]
            encounter = {"reference": f"Encounter/sh-e{eid}"}
            fields = {"status": "finished", "class": {
                "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode", "code": CLASSES[visit["encounter_type"]]},
                "type": [{"text": visit["encounter_type"]}], "subject": patient,
                "period": {"start": visit["encounter_date"]}}
            if visit["chief_complaint"]:
                fields["reasonCode"] = [{"text": visit["chief_complaint"]}]
            if visit["attending_name"]:
                # Scope providers per encounter: equal display names do not prove equal identity.
                provider_id = f"provider-e{eid}"
                yield resource("Practitioner", provider_id, name=[{"text": visit["attending_name"]}])
                fields["participant"] = [{"individual": {"reference": f"Practitioner/sh-{provider_id}"}}]
            yield resource("Encounter", f"e{eid}", **fields)
            if visit["note_text"]:
                yield document(f"note-e{eid}", patient, visit["note_text"],
                               "Clinical encounter note", encounter)


def export(source, output, split="train", tasks_dir=None):
    if not source.is_file():
        raise ValueError(f"Missing source database: {source}")
    output.mkdir(parents=True, exist_ok=True)
    counts = Counter()
    # Atomic output: failed conversions cannot leave partial NDJSON for the loader.
    with tempfile.NamedTemporaryFile(mode="w", dir=output, suffix=".tmp", delete=False) as out:
        temporary = Path(out.name)
        try:
            with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                db.row_factory = sqlite3.Row
                for item in chart_resources(db, split):
                    out.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
                    counts[item["resourceType"]] += 1
            if not counts["Patient"]:
                raise ValueError(f"No patients found for split {split}")
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    temporary.chmod(0o644)  # The Rust loader runs as a different, unprivileged container user.
    temporary.replace(output / "resources.ndjson")
    source_digest = checksum(source)
    manifest = {"source": SOURCE, "revision": REVISION if source_digest == SHA256 else None, "source_sha256": source_digest,
                "split": split, "counts": dict(sorted(counts.items()))}
    # Non-JSON extension keeps the generic FHIR loader from scanning metadata as resources.
    (output / "manifest.txt").write_text(json.dumps(manifest, indent=2) + "\n")
    if tasks_dir:
        tasks_dir.mkdir(parents=True, exist_ok=True)
        tasks = [{"id": f"sh-count-{kind}", "type": ["read"],
                  "meta": {"dataset": "synthetic_hospital", "split": split},
                  "input": {"task": f"How many {kind} resources are in the FHIR database? Do not use commas in the numbers.", "context": ""},
                  "output": {"answer": count}} for kind, count in sorted(counts.items())]
        (tasks_dir / "synthetic_hospital_counts.json").write_text(json.dumps(tasks, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("/data/benchmark_v1.3.db"))
    parser.add_argument("--output", type=Path, default=Path("/output"))
    parser.add_argument("--split", choices=["train", "public", "heldout", "all"], default=os.getenv("SYNTHETIC_HOSPITAL_SPLIT", "train"))
    parser.add_argument("--download", action="store_true", help="Download and checksum the pinned release")
    parser.add_argument("--tasks-dir", type=Path)
    args = parser.parse_args()
    if args.download:
        download(args.source)
    print(json.dumps(export(args.source, args.output, args.split, args.tasks_dir), indent=2))


if __name__ == "__main__":
    main()
