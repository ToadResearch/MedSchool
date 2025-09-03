# mcp_server/utils/terminology_client.py
"""Tiny helper around the FHIR `$lookup` operation.

• Uses the public HL7 terminology server by default.
• Falls back to other servers if you set TERMINOLOGY_BASE_URL.

Now returns normalized error dicts on non-2xx so tools can surface real error bodies into chat.
"""
from __future__ import annotations

import httpx
from typing import Any, Dict
from ..config import get_settings

settings = get_settings()

HEADERS = {"Accept": "application/fhir+json"}


def _infer_system(code: str) -> str:
    """Best-effort guess of code system if caller omits it.

    Supports explicit prefixes like:
      • "LOINC|4548-4", "rxnorm:1049630", "SNOMEDCT:44054006"
      • "http://loinc.org|4548-4"  (returns the URL verbatim)

    Heuristics when no explicit prefix is provided:
      • LOINC: ####-# or LP####-# → http://loinc.org
      • ICD-10-CM: Letter + 2 digits with optional ".suffix" (e.g., E11.9, S93.401A, U07.1)
                   → http://hl7.org/fhir/sid/icd-10-cm
      • RxNorm: all digits, length ≤ 7 → http://www.nlm.nih.gov/research/umls/rxnorm
      • ICD-10-PCS: 7 allowed chars with at least one LETTER (no I/O) → http://hl7.org/fhir/sid/icd-10-pcs
      • SNOMED CT: all digits, length > 7 (default fallback too)

    Note:
      We check "purely numeric" BEFORE PCS to avoid classifying 7-digit RxNorm CUIs as PCS.
      PCS must contain at least one letter (positive lookahead) to further reduce collisions.
    """
    s = (code or "").strip()
    if not s:
        return "http://snomed.info/sct"

    # Check for explicit system prefix (FHIR-style "system|code" or "alias:code").
    prefix = None
    if "|" in s:
        prefix, _ = s.split("|", 1)
    elif ":" in s and not s.lower().startswith("http"):
        parts = s.split(":", 1)
        if len(parts) == 2:
            prefix = parts[0]

    if prefix:
        key = prefix.strip().lower()
        alias_map = {
            "loinc": "http://loinc.org",
            "lnc": "http://loinc.org",
            "snomed": "http://snomed.info/sct",
            "snomedct": "http://snomed.info/sct",
            "sct": "http://snomed.info/sct",
            "icd10cm": "http://hl7.org/fhir/sid/icd-10-cm",
            "icd-10-cm": "http://hl7.org/fhir/sid/icd-10-cm",
            "icd10": "http://hl7.org/fhir/sid/icd-10-cm",
            "icd10pcs": "http://hl7.org/fhir/sid/icd-10-pcs",
            "icd-10-pcs": "http://hl7.org/fhir/sid/icd-10-pcs",
            "pcs": "http://hl7.org/fhir/sid/icd-10-pcs",
            "rxnorm": "http://www.nlm.nih.gov/research/umls/rxnorm",
            "rxcui": "http://www.nlm.nih.gov/research/umls/rxnorm",
        }
        if key in alias_map:
            return alias_map[key]
        if key.startswith("http"):
            return prefix.strip()  # already canonical URL

    # Strip any prefix to get the bare code value
    val = s.split("|", 1)[-1]
    if ":" in val and not val.lower().startswith("http"):
        val = val.split(":", 1)[-1]
    v = val.strip()

    import re

    # LOINC:
    # - Classic numeric LOINC: ####-#
    # - LOINC Part numbers: LP####-#
    if re.match(r'^(?:\d{1,7}-\d{1,2}|LP\d{1,7}-\d{1,2})$', v, re.IGNORECASE):
        return "http://loinc.org"

    # ICD-10-CM (now including U-block, e.g., U07.1)
    if re.match(r'^[A-Z][0-9]{2}(?:\.[A-Z0-9]{1,4})?$', v):
        return "http://hl7.org/fhir/sid/icd-10-cm"

    # Purely numeric: prefer RxNorm for shorter IDs, else SNOMED CT
    if v.isdigit():
        return "http://www.nlm.nih.gov/research/umls/rxnorm" if len(v) <= 7 else "http://snomed.info/sct"

    # ICD-10-PCS: 7 chars from 0-9 A-H J-N P-Z (no I/O), and must include at least one letter.
    if re.match(r'^(?=.*[A-HJ-NP-Z])[0-9A-HJ-NP-Z]{7}$', v):
        return "http://hl7.org/fhir/sid/icd-10-pcs"

    # Default fallback
    return "http://snomed.info/sct"



def lookup(code: str, system: str | None = None) -> Dict[str, Any]:
    system = system or _infer_system(code)
    url = f"{settings.terminology_base_url.rstrip('/')}/CodeSystem/$lookup"
    params = {"code": code, "system": system}
    timeout_s = getattr(settings.limits.get("code_lookup"), "timeout_s", 10) or 10

    with httpx.Client(timeout=timeout_s) as client:
        r = client.get(url, params=params, headers=HEADERS)
        raw_text = r.text
        try:
            data = r.json()
        except Exception:
            data = None

        if r.status_code >= 400:
            return {
                "error": f"{r.status_code} {r.reason_phrase}",
                "error_body": data if isinstance(data, dict) else {"raw": raw_text[:4000]},
                "http_status": r.status_code,
                "url": str(r.request.url),
            }

        if not isinstance(data, dict):
            return {
                "error": "invalid_json",
                "error_body": {"raw": raw_text[:4000]},
                "http_status": r.status_code,
                "url": str(r.request.url),
            }

    # Success: extract display/version/designations as before, but keep debugging URL
    display: str | None = None
    version: str | None = None
    designations: list[str] = []

    for p in data.get("parameter", []):
        if p["name"] == "display":
            display = p.get("valueString")
        elif p["name"] == "version":
            version = p.get("valueString")
        elif p["name"] == "designation":
            for part in p.get("part", []):
                if part["name"] == "value" and "valueString" in part:
                    designations.append(part["valueString"])

    return {
        "system": system,
        "code": code,
        "display": display,
        "version": version,
        "synonyms": designations,
        "url": str(r.request.url),  # include the effective request URL for debug
    }
