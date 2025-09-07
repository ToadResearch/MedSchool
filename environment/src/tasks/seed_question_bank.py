#!/usr/bin/env python
# environment/src/tasks/seed_question_bank.py
"""
Create a starter QA dataset by sampling real patients from the FHIR server.
Uses your AppConfig + AsyncFHIRClient (no tokens, no MCP).

USAGE (recommended):
  python -m environment.src.tasks.seed_question_bank --out environment/src/tasks/qa_dataset.yaml --patients 8

Or run directly:
  python environment/src/tasks/seed_question_bank.py --out environment/src/tasks/qa_dataset.yaml
"""
from __future__ import annotations

import argparse
import asyncio
import random
import uuid
from pathlib import Path
from typing import Any, Dict, List

import yaml

# Robust imports
try:
    from ..config import AppConfig
    from ..clients.fhir_client import AsyncFHIRClient
except Exception:
    # Fallback for direct execution
    import sys as _sys
    _ROOT = Path(__file__).resolve().parents[3]
    if str(_ROOT) not in _sys.path:
        _sys.path.insert(0, str(_ROOT))
    from environment.src.config import AppConfig
    from environment.src.clients.fhir_client import AsyncFHIRClient


TEMPLATE_QS: List[Dict[str, Any]] = [
    # Will be formatted with {"name": "<Patient Name>"} if available
    {"question": "What is {name}'s latest HbA1c value?", "code": "4548-4"},  # LOINC HbA1c
    {"question": "How many encounters has {name} had in the past year?", "code": None},
]


async def _fetch_patients(base_url: str, timeout_s: float, count: int = 10) -> List[Dict[str, str]]:
    """
    Return a list of patients with simple display name and id:
      [{"id":"123", "display":"Jane Doe"}, ...]
    """
    async with AsyncFHIRClient(base_url, timeout_s=timeout_s) as client:
        bundle = await client.search("Patient", {"_count": max(1, count)})
        out: List[Dict[str, str]] = []
        for e in (bundle or {}).get("entry", []) or []:
            r = (e or {}).get("resource") or {}
            pid = r.get("id")
            if not pid:
                continue
            display = ""
            if r.get("name"):
                n0 = r["name"][0]
                given = (n0.get("given") or [""])[0]
                family = n0.get("family") or ""
                display = " ".join([given, family]).strip()
            out.append({"id": str(pid), "display": display or f"Patient/{pid}"})
        return out


def _build_entries(patients: List[Dict[str, str]], k: int) -> List[Dict[str, Any]]:
    """
    Build K question entries across the provided patients.
    Each entry has a dummy answer_ids; you can later fill with real IDs
    (or use generate_task.py to auto-populate for specific codes).
    """
    if not patients:
        return []

    data: List[Dict[str, Any]] = []
    for _ in range(k):
        p = random.choice(patients)
        t = random.choice(TEMPLATE_QS)
        q = t["question"].format(name=p["display"])
        # Placeholders; can be replaced by generate_task.py later
        data.append(
            {
                "question": q,
                "patient_ref": f"Patient/{p['id']}",
                "answer_ids": [f"Observation/{uuid.uuid4()}"],  # placeholder
                "loinc_code": t["code"],
            }
        )
    return data


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out",
        default="environment/src/tasks/qa_dataset.yaml",
        help="Path to write the generated YAML (default: %(default)s)",
    )
    ap.add_argument("--patients", type=int, default=8, help="How many unique patients to sample (default: %(default)s)")
    ap.add_argument("--entries", type=int, default=8, help="How many QA entries to create (default: %(default)s)")
    args = ap.parse_args()

    cfg = AppConfig.load(timeout_s=30.0)
    patients = asyncio.run(_fetch_patients(cfg.fhir_base_url, timeout_s=cfg.timeout_s, count=args.patients))
    if not patients:
        raise SystemExit("No patients found; is your FHIR server reachable and seeded?")

    entries = _build_entries(patients, args.entries)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as f:
        yaml.safe_dump(entries, f, sort_keys=False)
    print(f"Wrote {len(entries)} Q-A pairs to {args.out}")


if __name__ == "__main__":
    main()
