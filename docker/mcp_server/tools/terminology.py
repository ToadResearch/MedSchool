# mcp_server/tools/terminology.py
from __future__ import annotations

from typing import Any, Dict, List

from ..mcp_app import mcp
from ..config import get_settings
from ..utils import terminology_client as tc

settings = get_settings()

# ────────────────────── Generic lookup ──────────────────────────
if "code_lookup" in settings.enabled:
    @mcp.tool(
        name="code_lookup",
        description=(
            "Return a JSON object with the display name (and any synonyms) "
            "for a code from SNOMED CT, ICD-10-CM, LOINC, RxNorm, etc.\n\n"
            "Args:\n"
            "  code: The code value, e.g. 'E11.9' or '44054006'.\n"
            "  system: Optional canonical system URI. "
            "If omitted, the server will guess from the code format.\n"
            "Returns: {system, code, display, version, synonyms[]}."
        ),
    )
    def code_lookup(code: str, system: str | None = None) -> Dict[str, Any]:
        result = tc.lookup(code, system)
        # If the client surfaced an error body, pass through verbatim so chat sees the details.
        if isinstance(result, dict) and result.get("error"):
            return result
        return result


# ────────────────── Simple hard-wired cross-walks ───────────────
if "snomed_to_icd10" in settings.enabled:
    @mcp.tool(
        name="snomed_to_icd10",
        description="Return candidate ICD-10-CM codes for a SNOMED CT concept code.",
    )
    def snomed_to_icd10(sct_code: str) -> List[str]:
        # ⚠️  stub – replace with real ConceptMap/$translate when available
        return ["E11.9"] if sct_code == "44054006" else []

if "icd10_to_snomed" in settings.enabled:
    @mcp.tool(
        name="icd10_to_snomed",
        description="Return candidate SNOMED CT concepts for a given ICD-10-CM code.",
    )
    def icd10_to_snomed(icd10: str) -> List[str]:
        return ["44054006"] if icd10.upper() == "E11.9" else []
