# mcp_server/utils/openfda_client.py
# https://open.fda.gov/about/status/
# modified from https://github.com/snap-stanford/Biomni/blob/main/biomni/tool/pharmacology.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

from ..config import get_settings

settings = get_settings()

BASE_URL = settings.openfda_base_url.rstrip("/")
USER_AGENT = os.getenv("OPENFDA_USER_AGENT", "medschool-mcp/1.0")

def _std_drug_name(name: str) -> str:
    """Light standardization for query consistency."""
    if not name:
        return ""
    s = name.strip().lower()
    # Remove common multi-word salt suffixes if present (heuristic only)
    for suf in (" sodium", " hydrochloride", " sulfate", " phosphate", " acetate", " citrate"):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    return s

def _get(endpoint: str, params: Dict[str, Any], timeout_s: int = 15) -> Dict[str, Any]:
    """
    Low-level GET wrapper for openFDA (or its proxied base).

    • Never raises on HTTP status; instead returns a normalized dict that includes:
      - results/meta for success (and url), or
      - error/error_body/http_status/url for failures.
    • Always attempts to parse JSON, even for non-2xx responses, so upstream tools
      can surface the real error body into chat.

    `endpoint` should be like "drug/event", "drug/label", "drug/enforcement", "drug/shortages".
    """
    # Middleman-compatible and direct-compatible path:
    #   BASE_URL + "/<endpoint>.json"
    url = f"{BASE_URL}/{endpoint}.json"

    with httpx.Client(timeout=timeout_s, headers={"User-Agent": USER_AGENT}) as client:
        r = client.get(url, params=params)

        raw_text = r.text
        try:
            data = r.json()
        except Exception:
            data = None

        # openFDA uses 404 for "no matches"
        if r.status_code == 404:
            return {
                "results": [],
                "meta": {"results": {"total": 0}},
                "http_status": 404,
                "url": str(r.request.url),
            }

        # For any non-2xx, include the parsed error body (or raw text) so callers can display it.
        if r.status_code >= 400:
            return {
                "results": [],
                "meta": {"results": {"total": 0}},
                "error": f"{r.status_code} {r.reason_phrase}",
                "error_body": (data if isinstance(data, dict) else {"raw": raw_text[:4000]}),
                "http_status": r.status_code,
                "url": str(r.request.url),
            }

        # 2xx: normalize payload to a dict and attach the request URL for debug.
        if not isinstance(data, dict):
            return {
                "results": [],
                "meta": {"results": {"total": 0}},
                "url": str(r.request.url),
            }

        # Rarely, openFDA may return a 200 with an "error" object; pass it through.
        if "error" in data and isinstance(data["error"], dict):
            return {
                "results": [],
                "meta": {"results": {"total": 0}},
                "error": "openfda_error_object",
                "error_body": data.get("error"),
                "http_status": r.status_code,
                "url": str(r.request.url),
            }

        data["url"] = str(r.request.url)
        return data

# -------- Public, minimal endpoints we need --------

# https://open.fda.gov/apis/drug/event/how-to-use-the-endpoint/
def query_adverse_events(
    drug_name: str,
    limit: int = 100,
    timeout_s: int = 15,
) -> Dict[str, Any]:
    """
    drug/event: search by medicinal product name in FAERS reports.
    """
    q_name = _std_drug_name(drug_name)
    params = {
        "search": f"patient.drug.medicinalproduct:{q_name}",
        "limit": max(1, min(int(limit), 1000)),
    }
    data = _get("drug/event", params, timeout_s=timeout_s)
    # Add a standard disclaimer OpenFDA repeats in docs
    data["disclaimer"] = (
        "FDA Disclaimer: FAERS reports are voluntary; counts do not imply causation or rates."
    )
    return data

# https://open.fda.gov/apis/drug/label/how-to-use-the-endpoint/
def query_drug_labels(
    drug_name: str,
    limit: int = 25,
    timeout_s: int = 15,
) -> Dict[str, Any]:
    """
    drug/label: search by brand name (openfda.brand_name).
    """
    q_name = _std_drug_name(drug_name)
    params = {
        "search": f"openfda.brand_name:{q_name}",
        "limit": max(1, min(int(limit), 100)),
    }
    return _get("drug/label", params, timeout_s=timeout_s)

# https://open.fda.gov/apis/drug/enforcement/how-to-use-the-endpoint/
def query_recalls(
    drug_name: str,
    limit: int = 100,
    timeout_s: int = 15,
) -> Dict[str, Any]:
    """
    drug/enforcement: search by brand name on recall/enforcement notices.
    """
    q_name = _std_drug_name(drug_name)
    params = {
        "search": f"openfda.brand_name:{q_name}",
        "limit": max(1, min(int(limit), 1000)),
    }
    return _get("drug/enforcement", params, timeout_s=timeout_s)

# https://open.fda.gov/apis/drug/drugshortages/how-to-use-the-endpoint/
def query_drug_shortages(
    drug_name: str,
    limit: int = 100,
    timeout_s: int = 15,
) -> Dict[str, Any]:
    """
    drug/shortages: search by proprietary/generic name (and a few other fields).
    We quote the values to follow openFDA's Lucene syntax and avoid parse exceptions.
    See: https://open.fda.gov/apis/query-syntax/
    """
    q_name = _std_drug_name(drug_name)

    # Quote/escape value for Lucene per openFDA query syntax.
    def _q(v: str) -> str:
        # Minimal escaping: backslash and double quote inside a quoted string.
        s = (v or "").replace("\\", "\\\\").replace('"', '\\"')
        return f'"{s}"'

    # OR across fields that actually exist on this dataset
    or_terms = [
        f"proprietary_name:{_q(q_name)}",
        f"generic_name:{_q(q_name)}",
        f"company_name:{_q(q_name)}",
    ]
    search = "(" + "+OR+".join(or_terms) + ")"

    params = {
        "search": search,
        # this dataset caps at 100 (docs)
        "limit": max(1, min(int(limit), 100)),
    }
    return _get("drug/shortages", params, timeout_s=timeout_s)

# -------- Convenience summarizers used by the tool layer --------

def summarize_adverse_events(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Small summary: total, serious %, top reactions."""
    results = payload.get("results") or []
    total = len(results)
    serious = 0
    reactions: Dict[str, int] = {}
    for row in results:
        if row.get("serious") == "1":
            serious += 1
        for rxn in (row.get("patient") or {}).get("reaction", []):
            term = rxn.get("reactionmeddrapt")
            if term:
                reactions[term] = reactions.get(term, 0) + 1

    top = sorted(reactions.items(), key=lambda kv: kv[1], reverse=True)[:10]
    return {
        "total_reports": total,
        "serious_reports": serious,
        "serious_pct": round((serious / total * 100.0), 1) if total else 0.0,
        "top_reactions": [{"term": t, "count": c} for t, c in top],
    }

def filter_adverse_events(
    payload: Dict[str, Any],
    severity: Optional[str] = None,  # "serious" | "non_serious" | None
    outcomes: Optional[List[str]] = None,  # e.g., ["death","hospitalization","life_threatening"]
) -> Dict[str, Any]:
    results = payload.get("results") or []
    def ok(row: Dict[str, Any]) -> bool:
        if severity == "serious" and row.get("serious") != "1":
            return False
        if severity == "non_serious" and row.get("serious") == "1":
            return False
        if outcomes:
            # map to FAERS fields
            checks = {
                "death": row.get("seriousnessdeath") == "1",
                "hospitalization": row.get("seriousnesshospitalization") == "1",
                "life_threatening": row.get("seriousnesslifethreatening") == "1",
            }
            if not any(checks.get(o, False) for o in outcomes):
                return False
        return True
    filtered = [r for r in results if ok(r)]
    return {
        "results": filtered,
        "meta": {"results": {"total": len(filtered)}},
        "disclaimer": payload.get("disclaimer"),
    }

def summarize_shortages(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Small roll-up: counts by status, distinct manufacturers/forms, and example products."""
    from collections import Counter

    rows = payload.get("results") or []
    status_counter = Counter()
    manufacturers: set[str] = set()
    forms: set[str] = set()
    examples: list[dict] = []

    for r in rows:
        status = r.get("status") or r.get("shortage_status") or "unknown"
        status_counter[status] += 1

        mf = (
            r.get("manufacturer_name")
            or (r.get("openfda") or {}).get("manufacturer_name")
        )
        if isinstance(mf, list):
            manufacturers.update([m for m in mf if m])
        elif isinstance(mf, str):
            manufacturers.add(mf)

        df = r.get("dosage_form") or (r.get("openfda") or {}).get("dosage_form")
        if isinstance(df, list):
            forms.update([d for d in df if d])
        elif isinstance(df, str):
            forms.add(df)

    # A few compact examples for UX
    for r in rows[:5]:
        examples.append({
            "brand_name": (r.get("brand_name")
                           or (r.get("openfda") or {}).get("brand_name")),
            "generic_name": (r.get("generic_name")
                             or (r.get("openfda") or {}).get("generic_name")),
            "dosage_form": r.get("dosage_form"),
            "strength": r.get("strength") or r.get("active_ingredients"),
            "status": r.get("status") or r.get("shortage_status"),
            "status_date": r.get("status_date")
                           or r.get("status_last_updated")
                           or r.get("status_date_updated"),
        })

    return {
        "total": len(rows),
        "status_counts": dict(status_counter),
        "distinct_manufacturers": sorted(manufacturers)[:20],
        "distinct_dosage_forms": sorted(forms)[:20],
        "examples": examples,
    }
