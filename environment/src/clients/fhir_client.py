# clients/fhir_client.py
from __future__ import annotations

import random
from typing import Any, Mapping, Optional

import httpx

from src.config import get_settings


class FHIRClientError(Exception):
    """Raised for FHIR client errors (HTTP and parsing)."""
    def __init__(self, message: str, status_code: int | None = None, response_text: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


def _is_operation_outcome(obj: Any) -> bool:
    return isinstance(obj, dict) and obj.get("resourceType") == "OperationOutcome" and isinstance(obj.get("issue"), list)

def _is_bundle(obj: Any) -> bool:
    return isinstance(obj, dict) and obj.get("resourceType") == "Bundle"


class FHIRClient:
    """Client for interacting with a FHIR server"""

    def __init__(self, base_url: Optional[str] = None, timeout_s: float = 30.0, client: Optional[httpx.AsyncClient] = None):
        if base_url is None:
            config = get_settings()
            base_url = config.fhir_base_url
        if not base_url:
            raise FHIRClientError("Missing FHIR base URL (check FHIR_* env vars)")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_s
        self._external_client = client
        self._client: Optional[httpx.AsyncClient] = None

        # explicit headers
        self._accept = {"Accept": "application/fhir+json"}
        self._json_ct = {"Content-Type": "application/fhir+json", **self._accept}

    async def __aenter__(self) -> "FHIRClient":
        if self._external_client is None and self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ---- helpers ---------------------------------------------------------

    def _url(self, *parts: str) -> str:
        return "/".join([self.base_url, *[p.strip("/") for p in parts]])

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        headers: Optional[dict[str, str]] = None,
    ) -> dict:
        """
        Internal HTTP wrapper that surfaces OperationOutcome details where possible.
        Creates a short-lived client when not in a managed context.
        """
        owns = False
        client = self._external_client or self._client
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            owns = True
        try:
            h = self._accept if json is None else self._json_ct
            if headers:
                h = {**h, **headers}

            resp = await client.request(method, url, headers=h, params=params, json=json)

            parsed_json: dict | None = None
            # Some FHIR endpoints (e.g., DELETE) may return 204 No Content
            if resp.content and resp.content.strip():
                try:
                    parsed_json = resp.json()
                except ValueError:
                    parsed_json = None

            if resp.status_code >= 400:
                # Prefer OperationOutcome if present
                msg = None
                if _is_operation_outcome(parsed_json):
                    issues = parsed_json.get("issue")  # type: ignore[union-attr]
                    msg = f"HTTP {resp.status_code}: OperationOutcome: {issues}"
                if msg is None:
                    msg = f"HTTP {resp.status_code}: {resp.text[:500]}"
                raise FHIRClientError(msg, status_code=resp.status_code, response_text=resp.text)

            # Accept empty success bodies (e.g., 204) by returning a minimal object
            if parsed_json is None:
                if resp.status_code in (200, 201, 202, 204):
                    return {"status": resp.status_code}
                raise FHIRClientError(f"Non-JSON response from {url}")

            return parsed_json

        finally:
            if owns:
                await client.aclose()

    # Generic helpers for arbitrary paths (needed by tools)
    async def get_path(self, path_or_query: str, params: Optional[Mapping[str, Any]] = None) -> dict:
        """
        GET anything after the base URL, e.g. 'Patient?name=Smith&_count=5' or 'Observation/123'.
        """
        from urllib.parse import parse_qsl
        s = path_or_query.lstrip("/")
        if "?" in s:
            path, qs = s.split("?", 1)
            merged = dict(parse_qsl(qs))
            if params:
                merged.update(params)
            return await self._request("GET", self._url(path), params=merged)
        return await self._request("GET", self._url(s), params=params)

    async def post_path(self, path: str, payload: Any) -> dict:
        """
        POST to a relative path (e.g., '', 'Patient', 'Patient/$validate').
        For transaction Bundles, POST to base ''.
        """
        url = self.base_url if not path or path.strip("/") == "" else self._url(path)
        return await self._request("POST", url, json=payload)

    async def put_path(self, path: str, payload: Any) -> dict:
        """
        PUT to a relative path (e.g., 'Patient/123').
        """
        if not path or path.strip("/") == "":
            raise FHIRClientError("PUT requires a concrete resource path like 'Type/id'")
        return await self._request("PUT", self._url(path), json=payload)

    async def delete_path(self, path: str, params: Optional[Mapping[str, Any]] = None) -> dict:
        """
        DELETE a resource at a relative path (e.g., 'Patient/123').
        """
        if not path or path.strip("/") == "":
            raise FHIRClientError("DELETE requires a concrete resource path like 'Type/id'")
        return await self._request("DELETE", self._url(path), params=params)

    # ---- public API (read-only + minimal write) --------------------------

    async def get_capability(self) -> dict:
        """GET [base]/metadata → CapabilityStatement"""
        return await self.get_path("metadata")

    async def read(self, resource_type: str, resource_id: str) -> dict:
        """GET [base]/{type}/{id}"""
        return await self.get_path(f"{resource_type}/{resource_id}")

    async def search(self, resource_type: str, params: Optional[Mapping[str, Any]] = None) -> dict:
        """GET [base]/{type}?<params>  → Bundle"""
        return await self.get_path(resource_type, params=params or {})

    async def count(self, resource_type: str, params: Optional[Mapping[str, Any]] = None) -> int:
        """Efficient count via `_summary=count&_total=accurate`."""
        p = {"_summary": "count", "_total": "accurate", **(params or {})}
        bundle = await self.search(resource_type, p)
        if not isinstance(bundle, dict):
            return 0
        # Try Bundle.total first; fall back to generic "total"
        if _is_bundle(bundle):
            try:
                return int(bundle.get("total") or 0)
            except Exception:
                return 0
        try:
            return int(bundle.get("total", 0) or 0)
        except Exception:
            return 0

    async def sample(self, resource_type: str, params: Optional[Mapping[str, Any]] = None) -> Optional[dict]:
        """
        Pick a random resource using count + offset paging (supported by HAPI).
        Returns the full resource JSON or None if empty.
        """
        base_params = dict(params or {})
        total = await self.count(resource_type, base_params)
        if total <= 0:
            return None

        index = random.randint(0, max(0, total - 1))
        p = {"_count": 1, "_offset": index, **base_params}
        bundle = await self.search(resource_type, p)

        if not isinstance(bundle, dict):
            return None

        entries = bundle.get("entry") or []
        if isinstance(entries, list) and entries:
            res = entries[0].get("resource") if isinstance(entries[0], dict) else None
            return res if isinstance(res, dict) else None
        return None

    async def search_by_identifier(
        self,
        resource_type: str,
        *,
        value: str,
        system: str | None = None,
        count: int = 10,
    ) -> list[dict]:
        """
        Read-only search by identifier (value or system|value).
        Returns a list of matching resources (may be empty).
        """
        assert resource_type and value
        ident = f"{system}|{value}" if system else value
        params = {
            "identifier": ident,
            "_count": str(max(1, int(count))),
            "_total": "accurate",
        }
        bundle = await self.search(resource_type, params)
        if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
            return []
        entries = bundle.get("entry") or []
        out = []
        for e in entries:
            if not isinstance(e, dict):
                continue
            r = e.get("resource")
            if isinstance(r, dict):
                out.append(r)
        return out

    async def read_by_identifier(
        self,
        resource_type: str,
        *,
        value: str,
        system: str | None = None,
    ) -> dict | None:
        """
        Convenience wrapper: return the first resource matching the identifier.
        """
        items = await self.search_by_identifier(resource_type, value=value, system=system, count=1)
        return items[0] if items else None
