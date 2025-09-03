# mcp_server/tools/fhir.py
from __future__ import annotations

import json
from typing import Any

from ..mcp_app import mcp
from ..config import get_settings
from ..utils.fhir_client import http_get, http_post

settings = get_settings()

# Helper to read per-tool limits safely
def _limit_for(tool_name: str):
    lim = settings.limits.get(tool_name)
    return lim if lim is not None else None

# ───────────────────────────── fhir_query ─────────────────────────────
if "fhir_query" in settings.enabled:
    @mcp.tool(
        name="fhir_query",
        description=(
            "HTTP GET / search against the FHIR server. "
            "`path` accepts anything after the base URL, "
            "e.g. 'Patient?name=Smith&_count=5' or 'Observation/123'. "
            "Returns compact JSON."
        ),
    )
    def fhir_query(path: str) -> str:
        data = http_get(path)

        # If the client surfaced an error/OperationOutcome, pass it through verbatim.
        if isinstance(data, dict) and data.get("error"):
            return json.dumps(data, separators=(",", ":"))

        # Truncate search bundles if a limit is configured
        lim = _limit_for("fhir_query")
        if (
            lim
            and getattr(lim, "max_results", None)
            and isinstance(data, dict)
            and data.get("resourceType") == "Bundle"
            and "entry" in data
            and isinstance(data["entry"], list)
        ):
            data["entry"] = data["entry"][: lim.max_results]
        return json.dumps(data, separators=(",", ":"))

# ─────────────────────────── fhir_submit_bundle ───────────────────────
if "fhir_submit_bundle" in settings.enabled:
    @mcp.tool(
        name="fhir_submit_bundle",
        description="POST a FHIR Bundle to the server (transaction). Returns the operation result.",
    )
    def fhir_submit_bundle(bundle_json: str) -> str:
        bundle = json.loads(bundle_json)
        data = http_post("", bundle)
        # Pass through error if present
        if isinstance(data, dict) and data.get("error"):
            return json.dumps(data, separators=(",", ":"))
        return json.dumps(data, separators=(",", ":"))

# ───────────────────────────── fhir_validate ──────────────────────────
if "fhir_validate" in settings.enabled:
    @mcp.tool(
        name="fhir_validate",
        description=(
            "Validate a resource against base profiles via $validate. "
            "Input is raw resource JSON string; returns OperationOutcome as dict."
        ),
    )
    def fhir_validate(resource_json: str) -> dict[str, Any]:
        resource = json.loads(resource_json)
        data = http_post("$validate", resource)
        # If the client detected an HTTP error, passthrough as-is (contains OperationOutcome if returned)
        if isinstance(data, dict) and data.get("error"):
            return data
        return data  # usually an OperationOutcome; return as-is

# ───────────────────────────────── fhir_doc ───────────────────────────
if "fhir_doc" in settings.enabled:
    @mcp.tool(
        name="fhir_doc",
        description="Return a short markdown cheat-sheet for any core R4 resource type.",
    )
    def fhir_doc(resource_type: str) -> str:  # pylint: disable=unused-argument
        # For demo, serve from local docstrings; production could read from files.
        docs: dict[str, str] = {
            "Patient": "### Patient\nKey elements: `identifier`, `name`, `gender`, `birthDate`, …",
            "Observation": "### Observation\nImportant fields: `code`, `value[x]`, `subject`, `effective[x]` …",
        }
        return docs.get(resource_type, f"No local docs for {resource_type}")
