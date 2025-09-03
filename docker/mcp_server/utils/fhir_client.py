# mcp_server/utils/fhir_client.py
from __future__ import annotations

import httpx
from ..config import get_settings

settings = get_settings()

HEADERS = {"Accept": "application/fhir+json"}
if settings.bearer_token:
    HEADERS["Authorization"] = f"Bearer {settings.bearer_token}"


def _timeout_s() -> int:
    # Use the configured fhir_query ToolLimit if present; default to 30s
    return getattr(settings.limits.get("fhir_query"), "timeout_s", 30) or 30


def http_get(path: str, params: dict | None = None) -> dict:
    """
    GET wrapper that never raises on HTTP status. Surfaces OperationOutcome/error bodies into the result.

    Returns:
      • Success: parsed JSON dict with an added "url" (the resolved request URL)
      • Error:   {"error": "<code reason>", "error_body": <parsed_json_or_raw>, "http_status": <int>, "url": <str>}
    """
    url = f"{settings.fhir_base_url.rstrip('/')}/{path.lstrip('/')}"

    print(f"\n\n\n\n\nurl: {url}")
    print(f"base fhir url: {settings.fhir_base_url.rstrip('/')}")

    t = _timeout_s()
    with httpx.Client(timeout=t) as client:
        r = client.get(url, params=params, headers=HEADERS)
        raw_text = r.text
        try:
            data = r.json()
        except Exception:
            data = None

        if r.status_code >= 400:
            # Surface the FHIR OperationOutcome (or raw body) to the tool layer.
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

        data["url"] = str(r.request.url)
        return data


def http_post(path: str, json_body: dict) -> dict:
    """
    POST wrapper that mirrors http_get behavior (no raise_for_status).
    Surfaces OperationOutcome/error bodies into the result.
    """
    url = f"{settings.fhir_base_url.rstrip('/')}/{path.lstrip('/')}"
    t = _timeout_s()
    with httpx.Client(timeout=t) as client:
        r = client.post(url, json=json_body, headers=HEADERS)
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

        data["url"] = str(r.request.url)
        return data
