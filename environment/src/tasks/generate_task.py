#!/usr/bin/env python
# environment/src/tasks/generate_task.py
"""
Populate *real* answer_ids for questions that target known LOINC codes or encounter counts.

Reads an existing qa_dataset.yaml, queries your FHIR server, and updates answer_ids
(where it can find a definitive answer) while leaving other fields intact.

USAGE:
  python -m environment.src.tasks.generate_task --in environment/src/tasks/qa_dataset.yaml --out environment/src/tasks/qa_dataset.filled.yaml

Supported patterns:
- "latest HbA1c" (loinc_code: 4548-4)  -> finds most recent Observation id for the patient
- "encounters in the past year"        -> returns Encounter ids within last 365 days
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

# Robust imports
try:
    from ..config import AppConfig
    from ..fhir_client import AsyncFHIRClient
except Exception:
    import sys as _sys
    _ROOT = Path(__file__).resolve().parents[3]
    if str(_ROOT) not in _sys.path:
        _sys.path.insert(0, str(_ROOT))
    from environment.src.config import AppConfig
    from environment.src.fhir_client import AsyncFHIRClient


async def _latest_observation_id_for_code(
    client: AsyncFHIRClient, patient_ref: str, loinc: str
) -> Optional[str]:
    """
    Return the id of the most recent Observation matching LOINC for the patient.
    """
    params = {
        "subject": patient_ref,
        "code": f"http://loinc.org|{loinc}",
        "_sort": "-date",  # HAPI supports sort on date if present
        "_count": 1,
    }
    bundle = await client.search("Observation", params)
    for e in (bundle or {}).get("entry", []) or []:
        r = (e or {}).get("resource") or {}
        rid = r.get("id")
        if rid:
            return f"Observation/{rid}"
    return None


async def _encounters_in_past_year(client: AsyncFHIRClient, patient_ref: str) -> List[str]:
    """
    Return Encounter ids for the last 365 days.
    """
    now = datetime.now(timezone.utc)
    since = (now - timedelta(days=365)).strftime("%Y-%m-%d")
    # Use date range over period.start|date (HAPI supports 'date' param)
    params = {"subject": patient_ref, "date": f"ge{since}", "_count": 200, "_total": "accurate"}
    bundle = await client.search("Encounter", params)
    out: List[str] = []
    for e in (bundle or {}).get("entry", []) or []:
        r = (e or {}).get("resource") or {}
        rid = r.get("id")
        if rid:
            out.append(f"Encounter/{rid}")
    return out


async def _process_entry(client: AsyncFHIRClient, entry: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fill answer_ids where we can determine a concrete answer.
    """
    patient_ref = entry.get("patient_ref")
    loinc = entry.get("loinc_code")
    qtext = (entry.get("question") or "").lower()

    # Pattern: latest HbA1c
    if loinc == "4548-4":
        obs_id = await _latest_observation_id_for_code(client, patient_ref, loinc)
        if obs_id:
            entry["answer_ids"] = [obs_id]
        return entry

    # Pattern: encounters in past year
    if "encounter" in qtext and "past year" in qtext:
        enc_ids = await _encounters_in_past_year(client, patient_ref)
        # Could be zero; keep the empty list as the truthy answer
        entry["answer_ids"] = enc_ids
        return entry

    # No known pattern — leave as is
    return entry


async def _fill_dataset(base_url: str, timeout_s: float, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    async with AsyncFHIRClient(base_url, timeout_s=timeout_s) as client:
        out: List[Dict[str, Any]] = []
        for row in data:
            try:
                out.append(await _process_entry(client, dict(row)))
            except Exception as e:
                # Preserve entry but note error
                r = dict(row)
                r["filling_error"] = str(e)
                out.append(r)
        return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="in_path", required=True, help="Input YAML path")
    ap.add_argument("--out", dest="out_path", required=True, help="Output YAML path")
    args = ap.parse_args()

    with open(args.in_path, "r") as f:
        data = yaml.safe_load(f) or []
        if not isinstance(data, list):
            raise SystemExit("Input YAML must be a list of question entries.")

    cfg = AppConfig.load(timeout_s=30.0)
    filled = asyncio.run(_fill_dataset(cfg.fhir_base_url, timeout_s=cfg.timeout_s, data=data))

    Path(args.out_path).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_path, "w") as f:
        yaml.safe_dump(filled, f, sort_keys=False)
    print(f"Updated {len(filled)} entries → {args.out_path}")


if __name__ == "__main__":
    main()
