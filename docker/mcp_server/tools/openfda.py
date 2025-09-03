# mcp_server/tools/openfda.py
# modified from https://github.com/snap-stanford/Biomni/blob/main/biomni/tool/pharmacology.py
from __future__ import annotations

from typing import Any, Dict, List, Optional
from ..mcp_app import mcp
from ..config import get_settings
from ..utils import openfda_client as fda

settings = get_settings()

def _timeout(tool_key: str = "openfda_drug_shortages"):
    lim = settings.limits.get(tool_key)
    return getattr(lim, "timeout_s", 15) or 15

# ───────────────────────── openfda_adverse_events ─────────────────────────
if "openfda_adverse_events" in settings.enabled:
    @mcp.tool(
        name="openfda_adverse_events",
        description=(
            "Search FDA FAERS (drug/event) by drug name and return a compact summary plus sample rows.\n\n"
            "Args:\n"
            "  drug_name (str): Brand or generic string.\n"
            "  limit (int, optional): Max rows to fetch from OpenFDA (default 100).\n"
            "  severity (str, optional): 'serious' | 'non_serious'.\n"
            "  outcomes (list[str], optional): Any of ['death','hospitalization','life_threatening'].\n"
            "  sample_n (int, optional): Include up to this many raw rows (default 5).\n"
            "Returns: JSON object with {summary, sample, meta, disclaimer}."
        ),
    )
    def openfda_adverse_events(
        drug_name: str,
        limit: int = 100,
        severity: str | None = None,
        outcomes: List[str] | None = None,
        sample_n: int = 5,
    ) -> Dict[str, Any]:
        raw = fda.query_adverse_events(drug_name, limit=limit, timeout_s=_timeout())
        if raw.get("error"):
            return {
                "error": raw.get("error"),
                "error_body": raw.get("error_body"),
                "http_status": raw.get("http_status"),
                "url": raw.get("url"),
            }

        # Optional filtering in-process
        filtered = fda.filter_adverse_events(raw, severity=severity, outcomes=outcomes) if (severity or outcomes) else raw
        summary = fda.summarize_adverse_events(filtered)
        # Keep sample small for chat UX
        sample = (filtered.get("results") or [])[: max(0, min(sample_n, 20))]
        return {
            "drug": drug_name,
            "summary": summary,
            "sample": sample,
            "meta": filtered.get("meta", {}),
            "disclaimer": filtered.get("disclaimer", raw.get("disclaimer")),
        }

# ─────────────────────────── openfda_label ──────────────────────────────
if "openfda_label" in settings.enabled:
    @mcp.tool(
        name="openfda_label",
        description=(
            "Fetch FDA drug label records (drug/label) for a brand/generic name and extract key sections.\n\n"
            "Args:\n"
            "  drug_name (str)\n"
            "  sections (list[str], optional): e.g., ['indications_and_usage','contraindications',"
            "'warnings','adverse_reactions','dosage_and_administration','clinical_pharmacology'].\n"
            "  limit (int, optional): Max label docs to fetch (default 10).\n"
            "Returns: JSON with {count, docs[]}, where each doc includes selected sections if present."
        ),
    )
    def openfda_label(
        drug_name: str,
        sections: List[str] | None = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        data = fda.query_drug_labels(drug_name, limit=limit, timeout_s=_timeout())
        if data.get("error"):
            return {
                "error": data.get("error"),
                "error_body": data.get("error_body"),
                "http_status": data.get("http_status"),
                "url": data.get("url"),
            }

        results = data.get("results") or []
        want = sections or [
            "indications_and_usage",
            "contraindications",
            "warnings",
            "adverse_reactions",
            "dosage_and_administration",
            "clinical_pharmacology",
        ]

        docs: List[Dict[str, Any]] = []
        for r in results:
            doc = {
                "effective_time": r.get("effective_time"),
                "brand_name": (r.get("openfda") or {}).get("brand_name"),
                "generic_name": (r.get("openfda") or {}).get("generic_name"),
                "manufacturer_name": (r.get("openfda") or {}).get("manufacturer_name"),
            }
            # Copy only selected sections if present; truncate long lists a bit
            for key in want:
                if key in r:
                    v = r[key]
                    if isinstance(v, list):
                        v = " ".join(v)
                    # Keep outputs modest for chat—trim very long blobs.
                    if isinstance(v, str) and len(v) > 2000:
                        v = v[:2000] + "…"
                    doc[key] = v
            docs.append(doc)

        return {"drug": drug_name, "count": len(docs), "docs": docs}

# ────────────────────────── openfda_recalls ─────────────────────────────
if "openfda_recalls" in settings.enabled:
    @mcp.tool(
        name="openfda_recalls",
        description=(
            "Fetch FDA drug recalls/enforcement actions (drug/enforcement).\n\n"
            "Args:\n"
            "  drug_name (str)\n"
            "  limit (int, optional): Max recall notices (default 100)\n"
            "Returns: JSON list of recalls with key fields."
        ),
    )
    def openfda_recalls(
        drug_name: str,
        limit: int = 100,
    ) -> Dict[str, Any]:
        data = fda.query_recalls(drug_name, limit=limit, timeout_s=_timeout())
        if data.get("error"):
            return {
                "error": data.get("error"),
                "error_body": data.get("error_body"),
                "http_status": data.get("http_status"),
                "url": data.get("url"),
            }

        rows = []
        for r in data.get("results") or []:
            rows.append({
                "recall_number": r.get("recall_number"),
                "classification": r.get("classification"),
                "status": r.get("status"),
                "recall_initiation_date": r.get("recall_initiation_date"),
                "product_description": r.get("product_description"),
                "reason_for_recall": r.get("reason_for_recall"),
                "distribution_pattern": r.get("distribution_pattern"),
            })
        return {"drug": drug_name, "count": len(rows), "recalls": rows}

# ──────────────────────── openfda_drug_shortages ────────────────────────────
if "openfda_drug_shortages" in settings.enabled:
    @mcp.tool(
        name="openfda_drug_shortages",
        description=(
            "Fetch current/archived FDA drug shortage entries (drug/drugshortages) for a brand/generic name.\n\n"
            "Args:\n"
            "  drug_name (str): Brand or generic string to search.\n"
            "  limit (int, optional): Max rows to fetch (default 100).\n"
            "  status (str, optional): Filter in-process by status label (e.g., 'Current', 'Resolved').\n"
            "  sample_n (int, optional): Include up to this many compact rows (default 10).\n"
            "Returns: JSON with {summary, rows, meta}. Rows contain key fields useful in EHRs."
        ),
    )
    def openfda_drug_shortages(
        drug_name: str,
        limit: int = 100,
        status: str | None = None,
        sample_n: int = 10,
    ) -> Dict[str, Any]:
        data = fda.query_drug_shortages(drug_name, limit=limit, timeout_s=_timeout("openfda_drug_shortages"))
        if data.get("error"):
            return {
                "error": data.get("error"),
                "error_body": data.get("error_body"),
                "http_status": data.get("http_status"),
                "url": data.get("url"),
            }

        raw_rows = data.get("results") or []

        def _status_of(r: Dict[str, Any]) -> str:
            return (r.get("shortage_status") or r.get("status") or "").strip()

        rows = [r for r in raw_rows if (not status or _status_of(r).lower() == status.lower())]

        compact: List[Dict[str, Any]] = []
        for r in rows[: max(0, min(sample_n, 50))]:
            compact.append({
                "brand_name": r.get("proprietary_name"),
                "generic_name": r.get("generic_name"),
                "manufacturer_name": r.get("company_name"),
                "dosage_form": r.get("dosage_form"),
                "presentation": r.get("drug_presentation"),
                "ndc": r.get("package_ndc") or r.get("ndc"),
                "status": r.get("shortage_status") or r.get("status"),
                "status_date": r.get("shortage_last_updated")
                            or r.get("status_last_updated")
                            or r.get("status_date")
                            or r.get("status_date_updated"),
                "reason": r.get("shortage_reason") or r.get("reason"),
                "notes": (r.get("shortage_detail") or r.get("note") or "")
                            [:500] if isinstance(r.get("shortage_detail") or r.get("note"), str) else None,
            })

        summary = fda.summarize_shortages({"results": rows})
        return {"drug": drug_name, "summary": summary, "rows": compact, "meta": data.get("meta", {})}
