#!/usr/bin/env python3
"""
Summarize Synthea FHIR JSON data by extracting a SINGLE most relevant label per resource
and ranking the top-10 labels for each resourceType.

Example:
- Condition -> from resource['code'] (prefer text -> coding.display -> coding.code)
  => "Fractured dental filling (finding)"

Usage:
  python summarize_synthea_primary_labels.py \
    --input fhir \
    --output results.json \
    --workers 8
"""

import argparse
import json
from pathlib import Path
from collections import Counter, defaultdict
from multiprocessing import Pool, cpu_count
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

Json = Union[dict, list, str, int, float, bool, None]

# In priority order, which fields likely carry the "primary label" for each resource type.
# Each entry is a list of candidate fields to try in order. Values can be:
# - A string name of a field containing a CodeableConcept (preferred)
# - A tuple ("cc_list", <field_name>) when the field is a list of CodeableConcepts
# - A tuple ("encounter_class", "class") to handle the Encounter.class object
RESOURCE_LABEL_FIELDS: Dict[str, List[Union[str, Tuple[str, str]]]] = {
    # Clinical resources with clear code concepts
    "Condition": ["code", ("cc_list", "category"), "clinicalStatus", "verificationStatus"],
    "Observation": ["code", ("cc_list", "category")],
    "Procedure": ["code", ("cc_list", "category")],
    "DiagnosticReport": ["code", ("cc_list", "category")],
    "ServiceRequest": ["code", ("cc_list", "category")],
    "AllergyIntolerance": ["code", ("cc_list", "category")],
    "Immunization": ["vaccineCode", ("cc_list", "category")],
    "MedicationRequest": ["medicationCodeableConcept"],
    "MedicationStatement": ["medicationCodeableConcept"],
    "MedicationAdministration": ["medicationCodeableConcept"],
    "MedicationDispense": ["medicationCodeableConcept"],
    "CarePlan": [("cc_list", "category")],
    "CareTeam": [("cc_list", "category")],
    "Claim": ["type"],
    "Device": ["type"],
    "Encounter": [("cc_list", "type"), ("encounter_class", "class"), "serviceType"],
    "EpisodeOfCare": [("cc_list", "type")],
    "Goal": ["description", ("cc_list", "category")],  # Goal.description is a string; we handle strings as fallback
    "ProcedureRequest": ["code"],  # legacy alias of ServiceRequest (rare)
    # Demographic/administrative resources (best-effort labels)
    "Patient": ["maritalStatus", "gender"],
    "Practitioner": [("cc_list", "qualification")],  # qualification[].code may exist
    "Location": ["type"],
    "Organization": ["type"],
    "HealthcareService": [("cc_list", "category"), "type"],
    "Appointment": [("cc_list", "serviceCategory"), ("cc_list", "serviceType")],
}

def iter_json_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*.json"):
        if p.is_file():
            yield p

def load_json_maybe_ndjson(path: Path) -> Iterable[Json]:
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    if not text:
        return
    # Try standard JSON
    try:
        yield json.loads(text)
        return
    except Exception:
        pass
    # Try NDJSON
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue

def cc_best_label(cc: Any) -> Optional[str]:
    """
    Extract best label from a CodeableConcept-like object:
    Prefer .text, else first coding.display, else first coding.code
    """
    if not isinstance(cc, dict):
        return None
    # 1) text
    t = cc.get("text")
    if isinstance(t, str) and t.strip():
        return t.strip()
    # 2) coding[].display
    coding = cc.get("coding")
    if isinstance(coding, list):
        for c in coding:
            if isinstance(c, dict):
                dsp = c.get("display")
                if isinstance(dsp, str) and dsp.strip():
                    return dsp.strip()
        # 3) coding[].code
        for c in coding:
            if isinstance(c, dict):
                cod = c.get("code")
                if isinstance(cod, str) and cod.strip():
                    return cod.strip()
    return None

def encounter_class_label(cls_obj: Any) -> Optional[str]:
    """
    Encounter.class is not a CC but has code/display/system.
    Prefer display, else code.
    """
    if isinstance(cls_obj, dict):
        dsp = cls_obj.get("display")
        if isinstance(dsp, str) and dsp.strip():
            return dsp.strip()
        cod = cls_obj.get("code")
        if isinstance(cod, str) and cod.strip():
            return cod.strip()
    return None

def first_cc_in_list_label(lst: Any) -> Optional[str]:
    if isinstance(lst, list):
        for item in lst:
            lbl = cc_best_label(item)
            if lbl:
                return lbl
    return None

def pick_primary_label(resource: dict) -> Optional[str]:
    """
    Choose the single most relevant label for a resource using RESOURCE_LABEL_FIELDS.
    """
    rt = resource.get("resourceType")
    if not isinstance(rt, str):
        return None

    # Try resource-specific fields
    for field in RESOURCE_LABEL_FIELDS.get(rt, []):
        if isinstance(field, tuple):
            kind, name = field
            if kind == "cc_list":
                lbl = first_cc_in_list_label(resource.get(name))
                if lbl:
                    return lbl
            elif kind == "encounter_class":
                lbl = encounter_class_label(resource.get(name))
                if lbl:
                    return lbl
        else:
            val = resource.get(field)
            # direct CC
            lbl = cc_best_label(val)
            if lbl:
                return lbl
            # if it's a list of CCs even though not marked as cc_list, try best-effort
            if isinstance(val, list):
                lbl = first_cc_in_list_label(val)
                if lbl:
                    return lbl
            # if it's a scalar string (e.g., Goal.description), accept it
            if isinstance(val, str) and val.strip():
                return val.strip()

    # Generic fallback: try common CC-looking fields
    for generic in ("code", "type", "category"):
        lbl = cc_best_label(resource.get(generic))
        if lbl:
            return lbl
        lbl = first_cc_in_list_label(resource.get(generic))
        if lbl:
            return lbl

    # Last resort: maybe status helps distinguish
    for sfield in ("status", "clinicalStatus", "verificationStatus"):
        lbl = cc_best_label(resource.get(sfield))
        if lbl:
            return lbl
        sval = resource.get(sfield)
        if isinstance(sval, str) and sval.strip():
            return sval.strip()

    return None

def process_obj(obj: Json) -> Dict[str, Counter]:
    """
    Return {resourceType: Counter({label: count})}
    """
    out: Dict[str, Counter] = defaultdict(Counter)

    def handle_res(res: dict):
        rt = res.get("resourceType")
        if not isinstance(rt, str):
            return
        lbl = pick_primary_label(res)
        if lbl:
            out[rt][lbl] += 1

    if isinstance(obj, dict):
        if obj.get("resourceType") == "Bundle" and isinstance(obj.get("entry"), list):
            for e in obj["entry"]:
                if isinstance(e, dict) and isinstance(e.get("resource"), dict):
                    handle_res(e["resource"])
        else:
            handle_res(obj)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict):
                if item.get("resourceType") == "Bundle" and isinstance(item.get("entry"), list):
                    for e in item["entry"]:
                        if isinstance(e, dict) and isinstance(e.get("resource"), dict):
                            handle_res(e["resource"])
                else:
                    handle_res(item)
    return out

def merge_counts(a: Dict[str, Counter], b: Dict[str, Counter]) -> Dict[str, Counter]:
    for rt, ctr in b.items():
        a[rt].update(ctr)
    return a

def process_file(path: Path) -> Dict[str, Counter]:
    agg: Dict[str, Counter] = defaultdict(Counter)
    try:
        for obj in load_json_maybe_ndjson(path):
            part = process_obj(obj)
            merge_counts(agg, part)
    except Exception:
        # skip unreadable files, but don't kill workers
        pass
    return agg

def aggregate_folder(root: Path, workers: int) -> Dict[str, Counter]:
    paths = list(iter_json_files(root))
    if not paths:
        return {}
    if workers <= 1:
        agg: Dict[str, Counter] = defaultdict(Counter)
        for p in paths:
            merge_counts(agg, process_file(p))
        return agg
    with Pool(processes=workers) as pool:
        # chunksize tuned to balance overhead vs throughput
        chunksize = max(1, len(paths) // (workers * 4))
        parts = pool.map(process_file, paths, chunksize=chunksize)
    agg: Dict[str, Counter] = defaultdict(Counter)
    for part in parts:
        merge_counts(agg, part)
    return agg

def build_report(counters: Dict[str, Counter], topk: int = 10) -> Dict[str, Any]:
    report: Dict[str, Any] = {}
    for rt in sorted(counters.keys(), key=str.lower):
        top = counters[rt].most_common(topk)
        report[rt] = [{"label": lbl, "count": cnt} for lbl, cnt in top]
    return report

def main():
    ap = argparse.ArgumentParser(description="Rank top-10 primary labels per FHIR resource type.")
    ap.add_argument("--input", "-i", type=str, default="fhir",
                    help="Folder with Synthea FHIR JSON files")
    ap.add_argument("--output", "-o", type=str, default="dataset_info.json",
                    help="Output JSON path")
    ap.add_argument("--workers", "-w", type=int, default=max(1, cpu_count() // 2),
                    help="Number of worker processes")
    ap.add_argument("--topk", "-k", type=int, default=10, help="Top K per resource type")
    args = ap.parse_args()

    root = Path(args.input)
    if not root.exists():
        raise SystemExit(f"Input path does not exist: {root}")

    agg = aggregate_folder(root, max(1, args.workers))
    report = build_report(agg, topk=args.topk)

    out = Path(args.output)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {out.resolve()}")

if __name__ == "__main__":
    main()
