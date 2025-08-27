# mcp_server/utils/fhir_client.py
from __future__ import annotations

import httpx
from ..config import get_settings

settings = get_settings()

HEADERS = {"Accept": "application/fhir+json"}
if settings.bearer_token:
    HEADERS["Authorization"] = f"Bearer {settings.bearer_token}"


def http_get(path: str, params: dict | None = None) -> dict:
    url = f"{settings.fhir_base_url.rstrip('/')}/{path.lstrip('/')}"
    with httpx.Client(timeout=settings.limits.get("fhir_query", {}).timeout_s or 30) as client:
        r = client.get(url, params=params, headers=HEADERS)
        r.raise_for_status()
        return r.json()


def http_post(path: str, json_body: dict) -> dict:
    url = f"{settings.fhir_base_url.rstrip('/')}/{path.lstrip('/')}"
    with httpx.Client(timeout=30.0) as client:
        r = client.post(url, json=json_body, headers=HEADERS)
        r.raise_for_status()
        return r.json()
