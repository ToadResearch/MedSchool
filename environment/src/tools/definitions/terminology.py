# environment/src/tools/definitions/terminology.py
from __future__ import annotations

from typing import Any, Dict, List, Optional
import re
import httpx

from ...config import get_settings

_settings = get_settings()

# Default public HL7 terminology server (R4)
_DEFAULT_TERMINOLOGY_BASE = "https://tx.fhir.org/r4"

_HEADERS = {"Accept": "application/fhir+json"}


def _terminology_base_url() -> str:
    """
    Resolve the terminology server base:
      1) use class base from env (TERMINOLOGY_BASE_URL or TERMINOLOGY_PROXY_PUBLIC_BASE)
      2) fallback to HL7 public server.
    """
    base = _settings.base_url_for_class("TERMINOLOGY")
    return (base or _DEFAULT_TERMINOLOGY_BASE).rstrip("/")


def _timeout_s(tool_name: str, default: float = 10.0) -> float:
    lim = _settings.limit(tool_name)
    try:
        return float(lim.timeout_s) if lim and lim.timeout_s is not None else default
    except Exception:
        return default


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

    # explicit FHIR-style "system|code" or "alias:code"
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
            return prefix.strip()

    # strip any prefix to get bare code
    val = s.split("|", 1)[-1]
    if ":" in val and not val.lower().startswith("http"):
        val = val.split(":", 1)[-1]
    v = val.strip()

    # LOINC
    if re.match(r'^(?:\d{1,7}-\d{1,2}|LP\d{1,7}-\d{1,2})$', v, re.IGNORECASE):
        return "http://loinc.org"

    # ICD-10-CM (includes U block)
    if re.match(r'^[A-Z][0-9]{2}(?:\.[A-Z0-9]{1,4})?$', v):
        return "http://hl7.org/fhir/sid/icd-10-cm"

    # Purely numeric → RxNorm for short, else SNOMED CT
    if v.isdigit():
        return "http://www.nlm.nih.gov/research/umls/rxnorm" if len(v) <= 7 else "http://snomed.info/sct"

    # ICD-10-PCS (7 chars, no I/O, must include at least one letter)
    if re.match(r'^(?=.*[A-HJ-NP-Z])[0-9A-HJ-NP-Z]{7}$', v):
        return "http://hl7.org/fhir/sid/icd-10-pcs"

    # Default fallback
    return "http://snomed.info/sct"


async def _lookup_request(code: str, system: Optional[str], timeout: float, session_id: Optional[str] = None) -> Dict[str, Any]:
    base = _terminology_base_url()
    url = f"{base}/CodeSystem/$lookup"
    params = {"code": code, "system": system or _infer_system(code)}

    headers = dict(_HEADERS)
    if session_id:
        headers["x-session-id"] = session_id

    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.get(url, params=params, headers=headers)
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

        # Success: extract display/version/designations
        display: str | None = None
        version: str | None = None
        designations: list[str] = []

        for p in data.get("parameter", []):
            if p.get("name") == "display":
                display = p.get("valueString")
            elif p.get("name") == "version":
                version = p.get("valueString")
            elif p.get("name") == "designation":
                for part in p.get("part", []):
                    if part.get("name") == "value" and "valueString" in part:
                        designations.append(part["valueString"])

        return {
            "system": params["system"],
            "code": code,
            "display": display,
            "version": version,
            "synonyms": designations,
            "url": str(r.request.url),
        }


def register_tools(session_manager):
    """
    Returns a dict of tool_name -> callable.

    All callables are async and accept `session_id=...` for consistency with other tools.
    (The terminology lookup itself does not require the session, but the signature stays uniform.)
    """

    async def code_lookup(*, session_id: str, code: str, system: str = None) -> Dict[str, Any]:
        """
        Return a JSON object with the display name (and any synonyms) for a code
        from SNOMED CT, ICD-10-CM, LOINC, RxNorm, etc.

        Args:
            code (str): The code value, e.g. 'E11.9' or '44054006'.
            system (str, optional): Canonical system URL or alias; if omitted, inferred.

        Returns:
            Dict[str, Any]: {system, code, display, version, synonyms[], url} or an error object.
        """
        _ = session_id  # TODO: unused for now. consider having HAPI FHIR route this
        timeout = _timeout_s("code_lookup", default=10.0)
        return await _lookup_request(code, system, timeout, session_id=session_id)

    async def snomed_to_icd10(*, session_id: str, sct_code: str) -> List[str]:
        """
        Return candidate ICD-10-CM codes for a SNOMED CT concept code.
        (Stub - replace with ConceptMap/$translate when available.)
        """
        _ = session_id
        return ["E11.9"] if sct_code == "44054006" else []

    async def icd10_to_snomed(*, session_id: str, icd10: str) -> List[str]:
        """
        Return candidate SNOMED CT concepts for a given ICD-10-CM code.
        (Stub - replace with ConceptMap/$translate when available.)
        """
        _ = session_id
        return ["44054006"] if icd10.upper() == "E11.9" else []

    tools: Dict[str, Any] = {}
    if "code_lookup" in _settings.enabled:
        tools["code_lookup"] = code_lookup
    if "snomed_to_icd10" in _settings.enabled:
        tools["snomed_to_icd10"] = snomed_to_icd10
    if "icd10_to_snomed" in _settings.enabled:
        tools["icd10_to_snomed"] = icd10_to_snomed
    return tools
