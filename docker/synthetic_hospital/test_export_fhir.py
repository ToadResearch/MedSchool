import base64
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from export_fhir import export, download


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.db"
        with closing(sqlite3.connect(self.source)) as db, db:
            db.executescript("""
                CREATE TABLE longitudinal_patients(patient_id INTEGER, profile TEXT, sex TEXT);
                CREATE TABLE benchmark_ground_truth(patient_id INTEGER, split TEXT, ground_truth TEXT);
                CREATE TABLE longitudinal_encounters(encounter_id INTEGER, patient_id INTEGER,
                    encounter_order INTEGER, encounter_type TEXT, encounter_date TEXT,
                    chief_complaint TEXT, attending_name TEXT, note_text TEXT);
            """)
            for pid, split in [(1, "train"), (2, "heldout"), (3, "public")]:
                profile = {"age_at_first_encounter": 40, "chronic_conditions": ["Documented history"],
                           "allergies": ["NKDA", "Latex"], "home_medications": [{"name": "Aspirin", "dose": "81 mg daily"}]}
                db.execute("INSERT INTO longitudinal_patients VALUES (?, ?, 'F')", (pid, json.dumps(profile)))
                # Multiple labels for the same patient must not duplicate resources.
                for _ in range(2):
                    db.execute("INSERT INTO benchmark_ground_truth VALUES (?, ?, ?)", (pid, split, 'SECRET DIAGNOSIS'))
                db.execute("INSERT INTO longitudinal_encounters VALUES (?, ?, 0, 'ed', '2020-01-01', 'Pain', 'Dr. Example', ?)",
                           (pid, pid, 'Original note with µ and <text>'))

    def run_export(self, split="train"):
        manifest = export(self.source, self.root / "out", split, self.root / "tasks")
        resources = [json.loads(line) for line in (self.root / "out/resources.ndjson").read_text().splitlines()]
        return manifest, resources

    def test_chart_content_and_reference_integrity(self):
        manifest, resources = self.run_export()
        self.assertEqual(manifest["counts"]["Patient"], 1)
        self.assertEqual((self.root / "out/resources.ndjson").stat().st_mode & 0o777, 0o644)
        keys = {r["resourceType"] + "/" + r["id"] for r in resources}
        self.assertEqual(len(keys), len(resources))
        def check(value):
            if isinstance(value, dict):
                if "reference" in value:
                    self.assertIn(value["reference"], keys)
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
        check(resources)
        self.assertNotIn("SECRET DIAGNOSIS", json.dumps(resources))
        patient = next(r for r in resources if r["resourceType"] == "Patient")
        self.assertNotIn("name", patient)
        self.assertNotIn("birthDate", patient)
        self.assertEqual(manifest["counts"]["AllergyIntolerance"], 1)
        note = next(r for r in resources if r["id"] == "sh-note-e1")
        self.assertEqual(base64.b64decode(note["content"][0]["attachment"]["data"]).decode(), 'Original note with µ and <text>')
        tasks = json.loads((self.root / "tasks/synthetic_hospital_counts.json").read_text())
        self.assertEqual(len(tasks), len(manifest["counts"]))

    def test_splits_and_repeatability(self):
        self.run_export()
        first = (self.root / "out/resources.ndjson").read_bytes()
        self.run_export()
        self.assertEqual(first, (self.root / "out/resources.ndjson").read_bytes())
        for split, count in [("public", 1), ("heldout", 1), ("all", 3)]:
            manifest, _ = self.run_export(split)
            self.assertEqual(manifest["counts"]["Patient"], count)

    def test_failure_preserves_previous_export(self):
        self.run_export()
        first = (self.root / "out/resources.ndjson").read_bytes()
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute("UPDATE longitudinal_encounters SET encounter_type='unsupported'")
        with self.assertRaises(KeyError):
            self.run_export()
        self.assertEqual(first, (self.root / "out/resources.ndjson").read_bytes())

    def test_missing_source_does_not_create_empty_database(self):
        missing = self.root / "missing.db"
        with self.assertRaises(ValueError):
            export(missing, self.root / "out")
        self.assertFalse(missing.exists())

    def test_checksum_failure_does_not_replace_cached_source(self):
        from io import BytesIO
        cached = self.root / "cached.db"
        cached.write_bytes(b"previous")
        with patch("urllib.request.urlopen", return_value=BytesIO(b"corrupted")):
            with self.assertRaises(ValueError):
                download(cached)
        self.assertEqual(cached.read_bytes(), b"previous")
        self.assertFalse(cached.with_suffix(".download").exists())


if __name__ == "__main__":
    unittest.main()
