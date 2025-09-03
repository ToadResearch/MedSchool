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
    """Best-effort guess of code system if caller omits it."""
    if code.isdigit():                       # 4548-4 → LOINC
        return "http://loinc.org"
    if "." in code and code[0].isalpha():    # E11.9 → ICD-10-CM
        return "http://hl7.org/fhir/sid/icd-10-cm"
    return "http://snomed.info/sct"          # default to SNOMED


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
