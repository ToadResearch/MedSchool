# environment/src/tools/definitions/openfda.py
# modified from https://github.com/snap-stanford/Biomni/blob/main/biomni/tool/pharmacology.py
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import httpx

from ...config import get_settings

_settings = get_settings()

# ---------- Config / defaults ----------

def _openfda_base_url() -> str:
    """
    Resolve base URL via class OPENFDA:
      • OPENFDA_BASE_URL or OPENFDA_PROXY_PUBLIC_BASE (from env)
      • fallback: https://api.fda.gov
    """
    return (_settings.base_url_for_class("OPENFDA") or "https://api.fda.gov").rstrip("/")

_USER_AGENT = "medschool-mcp/1.0"


def _timeout(tool_key: str, default: float = 15.0) -> float:
    lim = _settings.limit(tool_key)
    try:
        return float(lim.timeout_s) if lim and lim.timeout_s is not None else default
    except Exception:
        return default


# ---------- Small helpers ----------

def _std_drug_name(name: str) -> str:
    """Light standardization for query consistency."""
    if not name:
        return ""
    s = name.strip().lower()
    for suf in (" sodium", " hydrochloride", " sulfate", " phosphate", " acetate", " citrate"):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    return s


async def _get(endpoint: str, params: Dict[str, Any], timeout_s: float, session_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Low-level GET wrapper for openFDA (or its proxied base).

    • Never raises on HTTP status; returns normalized dict:
        - success: includes parsed data + 'url'
        - error : {error, error_body, http_status, url}
    • Always tries to parse JSON even for non-2xx responses.

    `endpoint` like: "drug/event", "drug/label", "drug/enforcement", "drug/shortages".
    """
    url = f"{_openfda_base_url()}/{endpoint}.json"
    headers = {"User-Agent": _USER_AGENT}
    if session_id:
        headers["x-session-id"] = session_id

    async with httpx.AsyncClient(timeout=timeout_s, headers=headers) as client:
        r = await client.get(url, params=params)

        raw_text = r.text
        try:
            data = r.json()
        except Exception:
            data = None

        # openFDA uses 404 for "no matches"
        if r.status_code == 404:
            return {"results": [], "meta": {"results": {"total": 0}}, "http_status": 404, "url": str(r.request.url)}

        if r.status_code >= 400:
            return {
                "results": [],
                "meta": {"results": {"total": 0}},
                "error": f"{r.status_code} {r.reason_phrase}",
                "error_body": (data if isinstance(data, dict) else {"raw": raw_text[:4000]}),
                "http_status": r.status_code,
                "url": str(r.request.url),
            }

        if not isinstance(data, dict):
            return {"results": [], "meta": {"results": {"total": 0}}, "url": str(r.request.url)}

        # Occasionally openFDA returns 200 with an "error" object; pass through.
        if isinstance(data.get("error"), dict):
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


# ---------- Endpoint-level query helpers ----------

async def _query_adverse_events(drug_name: str, limit: int, timeout_s: float, session_id: str) -> Dict[str, Any]:
    q_name = _std_drug_name(drug_name)
    params = {"search": f"patient.drug.medicinalproduct:{q_name}", "limit": max(1, min(int(limit), 1000))}
    data = await _get("drug/event", params, timeout_s=timeout_s, session_id=session_id)
    data["disclaimer"] = "FDA Disclaimer: FAERS reports are voluntary; counts do not imply causation or rates."
    return data


async def _query_drug_labels(drug_name: str, limit: int, timeout_s: float, session_id: str) -> Dict[str, Any]:
    q_name = _std_drug_name(drug_name)
    params = {"search": f"openfda.brand_name:{q_name}", "limit": max(1, min(int(limit), 100))}
    return await _get("drug/label", params, timeout_s=timeout_s, session_id=session_id)


async def _query_recalls(drug_name: str, limit: int, timeout_s: float, session_id: str) -> Dict[str, Any]:
    q_name = _std_drug_name(drug_name)
    params = {"search": f"openfda.brand_name:{q_name}", "limit": max(1, min(int(limit), 1000))}
    return await _get("drug/enforcement", params, timeout_s=timeout_s, session_id=session_id)


async def _query_drug_shortages(drug_name: str, limit: int, timeout_s: float, session_id: str) -> Dict[str, Any]:
    q_name = _std_drug_name(drug_name)

    def _q(v: str) -> str:
        s = (v or "").replace("\\", "\\\\").replace('"', '\\"')
        return f'"{s}"'

    or_terms = [f"proprietary_name:{_q(q_name)}", f"generic_name:{_q(q_name)}", f"company_name:{_q(q_name)}"]
    search = "(" + "+OR+".join(or_terms) + ")"
    params = {"search": search, "limit": max(1, min(int(limit), 100))}  # dataset caps ~100
    # Note: Older docs used "drug/drugshortages"; the provided legacy used "drug/shortages".
    return await _get("drug/shortages", params, timeout_s=timeout_s, session_id=session_id)


# ---------- Convenience summarizers / filters ----------

def _summarize_adverse_events(payload: Dict[str, Any]) -> Dict[str, Any]:
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


def _filter_adverse_events(
    payload: Dict[str, Any],
    severity: Optional[str] = None,                # "serious" | "non_serious" | None
    outcomes: Optional[List[str]] = None,          # e.g., ["death","hospitalization","life_threatening"]
) -> Dict[str, Any]:
    results = payload.get("results") or []

    def ok(row: Dict[str, Any]) -> bool:
        if severity == "serious" and row.get("serious") != "1":
            return False
        if severity == "non_serious" and row.get("serious") == "1":
            return False
        if outcomes:
            checks = {
                "death": row.get("seriousnessdeath") == "1",
                "hospitalization": row.get("seriousnesshospitalization") == "1",
                "life_threatening": row.get("seriousnesslifethreatening") == "1",
            }
            if not any(checks.get(o, False) for o in outcomes):
                return False
        return True

    filtered = [r for r in results if ok(r)]
    return {"results": filtered, "meta": {"results": {"total": len(filtered)}}, "disclaimer": payload.get("disclaimer")}


def _summarize_shortages(payload: Dict[str, Any]) -> Dict[str, Any]:
    from collections import Counter

    rows = payload.get("results") or []
    status_counter = Counter()
    manufacturers: set[str] = set()
    forms: set[str] = set()
    examples: list[dict] = []

    for r in rows:
        status = r.get("status") or r.get("shortage_status") or "unknown"
        status_counter[status] += 1

        mf = r.get("manufacturer_name") or (r.get("openfda") or {}).get("manufacturer_name")
        if isinstance(mf, list):
            manufacturers.update([m for m in mf if m])
        elif isinstance(mf, str) and mf:
            manufacturers.add(mf)

        df = r.get("dosage_form") or (r.get("openfda") or {}).get("dosage_form")
        if isinstance(df, list):
            forms.update([d for d in df if d])
        elif isinstance(df, str) and df:
            forms.add(df)

    for r in rows[:5]:
        examples.append({
            "brand_name": r.get("brand_name") or (r.get("openfda") or {}).get("brand_name"),
            "generic_name": r.get("generic_name") or (r.get("openfda") or {}).get("generic_name"),
            "dosage_form": r.get("dosage_form"),
            "strength": r.get("strength") or r.get("active_ingredients"),
            "status": r.get("status") or r.get("shortage_status"),
            "status_date": r.get("status_date") or r.get("status_last_updated") or r.get("status_date_updated"),
        })

    return {
        "total": len(rows),
        "status_counts": dict(status_counter),
        "distinct_manufacturers": sorted(manufacturers)[:20],
        "distinct_dosage_forms": sorted(forms)[:20],
        "examples": examples,
    }


# ---------- Tool registration (session-aware, async) ----------

def register_tools(session_manager):
    """
    Returns a dict of tool_name -> callable (all async).
    Each callable accepts `session_id` for uniformity with other tools.
    """

    async def openfda_adverse_events(
        *,
        session_id: str,
        drug_name: str,
        limit: int = 100,
        severity: Optional[str] = None,                    # 'serious' | 'non_serious'
        outcomes: Optional[List[str]] = None,              # any of ['death','hospitalization','life_threatening']
        sample_n: int = 5,
    ) -> Dict[str, Any]:
        """
        Search FDA FAERS (drug/event) by drug name and return a compact summary plus sample rows.

        Args:
            session_id (str): Current session id (not used directly).
            drug_name (str): Brand or generic string.
            limit (int, optional): Max rows to fetch from OpenFDA (default 100).
            severity (str, optional): 'serious' | 'non_serious'.
            outcomes (list[str], optional): Any of ['death','hospitalization','life_threatening'].
            sample_n (int, optional): Include up to this many raw rows (default 5).

        Returns:
            JSON object with {summary, sample, meta, disclaimer} or error object.
        """
        _ = session_id
        raw = await _query_adverse_events(drug_name, limit=limit, timeout_s=_timeout("openfda_adverse_events", 15), session_id=session_id)
        if raw.get("error"):
            return {"error": raw.get("error"), "error_body": raw.get("error_body"), "http_status": raw.get("http_status"), "url": raw.get("url")}

        filtered = _filter_adverse_events(raw, severity=severity, outcomes=outcomes) if (severity or outcomes) else raw
        summary = _summarize_adverse_events(filtered)
        sample = (filtered.get("results") or [])[: max(0, min(sample_n, 20))]
        return {"drug": drug_name, "summary": summary, "sample": sample, "meta": filtered.get("meta", {}), "disclaimer": filtered.get("disclaimer", raw.get("disclaimer"))}

    async def openfda_label(
        *,
        session_id: str,
        drug_name: str,
        sections: Optional[List[str]] = None,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """
        Fetch FDA drug label records (drug/label) for a brand/generic name and extract key sections.

        Args:
            session_id (str): Current session id (not used directly).
            drug_name (str)
            sections (list[str], optional): e.g., ['indications_and_usage','contraindications','warnings',
                'adverse_reactions','dosage_and_administration','clinical_pharmacology'].
            limit (int, optional): Max label docs to fetch (default 10).

        Returns:
            JSON with {count, docs[]} or error object.
        """
        _ = session_id
        data = await _query_drug_labels(drug_name, limit=limit, timeout_s=_timeout("openfda_label", 15), session_id=session_id)
        if data.get("error"):
            return {"error": data.get("error"), "error_body": data.get("error_body"), "http_status": data.get("http_status"), "url": data.get("url")}

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
            doc: Dict[str, Any] = {
                "effective_time": r.get("effective_time"),
                "brand_name": (r.get("openfda") or {}).get("brand_name"),
                "generic_name": (r.get("openfda") or {}).get("generic_name"),
                "manufacturer_name": (r.get("openfda") or {}).get("manufacturer_name"),
            }
            for key in want:
                if key in r:
                    v = r[key]
                    if isinstance(v, list):
                        v = " ".join(v)
                    if isinstance(v, str) and len(v) > 2000:
                        v = v[:2000] + "…"
                    doc[key] = v
            docs.append(doc)

        return {"drug": drug_name, "count": len(docs), "docs": docs}

    async def openfda_recalls(
        *,
        session_id: str,
        drug_name: str,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        Fetch FDA drug recalls/enforcement actions (drug/enforcement).

        Args:
            session_id (str): Current session id (not used directly).
            drug_name (str)
            limit (int, optional): Max recall notices (default 100)

        Returns:
            JSON list with key fields or error object.
        """
        _ = session_id
        data = await _query_recalls(drug_name, limit=limit, timeout_s=_timeout("openfda_recalls", 15), session_id=session_id)
        if data.get("error"):
            return {"error": data.get("error"), "error_body": data.get("error_body"), "http_status": data.get("http_status"), "url": data.get("url")}

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

    async def openfda_drug_shortages(
        *,
        session_id: str,
        drug_name: str,
        limit: int = 100,
        status: Optional[str] = None,
        sample_n: int = 10,
    ) -> Dict[str, Any]:
        """
        Fetch current/archived FDA drug shortage entries (drug/drugshortages) for a brand/generic name.

        Args:
            session_id (str): Current session id (not used directly).
            drug_name (str)
            limit (int, optional): Max rows to fetch (default 100).
            status (str, optional): Filter in-process by status label (e.g., 'Current', 'Resolved').
            sample_n (int, optional): Include up to this many compact rows (default 10).

        Returns:
            JSON with {summary, rows, meta} or error object.
        """
        _ = session_id
        data = await _query_drug_shortages(drug_name, limit=limit, timeout_s=_timeout("openfda_drug_shortages", 15), session_id=session_id)
        if data.get("error"):
            return {"error": data.get("error"), "error_body": data.get("error_body"), "http_status": data.get("http_status"), "url": data.get("url")}

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
                "status_date": r.get("shortage_last_updated") or r.get("status_last_updated") or r.get("status_date") or r.get("status_date_updated"),
                "reason": r.get("shortage_reason") or r.get("reason"),
                "notes": (r.get("shortage_detail") or r.get("note") or "")[:500] if isinstance(r.get("shortage_detail") or r.get("note"), str) else None,
            })

        summary = _summarize_shortages({"results": rows})
        return {"drug": drug_name, "summary": summary, "rows": compact, "meta": data.get("meta", {})}

    tools: Dict[str, Any] = {}
    if "openfda_adverse_events" in _settings.enabled:
        tools["openfda_adverse_events"] = openfda_adverse_events
    if "openfda_label" in _settings.enabled:
        tools["openfda_label"] = openfda_label
    if "openfda_recalls" in _settings.enabled:
        tools["openfda_recalls"] = openfda_recalls
    if "openfda_drug_shortages" in _settings.enabled:
        tools["openfda_drug_shortages"] = openfda_drug_shortages
    return tools