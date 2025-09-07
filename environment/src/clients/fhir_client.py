from __future__ import annotations

import random
from typing import Any, Mapping, Optional

import httpx
from pydantic import BaseModel, Field, ValidationError


class FHIRClientError(RuntimeError):
    pass


class _ParsedBundle(BaseModel):
    resourceType: str = Field(pattern="^Bundle$")
    total: Optional[int] = None
    entry: Optional[list[dict]] = None


class _OperationOutcome(BaseModel):
    resourceType: str = Field(pattern="^OperationOutcome$")
    issue: list[dict]


class AsyncFHIRClient:
    """
    Minimal, async, read-only FHIR client for HAPI (R4).
    Uses the public proxy URL so it can run outside the Docker network.
    """

    def __init__(self, base_url: str, timeout_s: float = 30.0, client: Optional[httpx.AsyncClient] = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_s
        self._external_client = client
        self._client: Optional[httpx.AsyncClient] = None

        # explicit headers
        self._accept = {"Accept": "application/fhir+json"}
        self._json_ct = {"Content-Type": "application/fhir+json", **self._accept}

    async def __aenter__(self) -> "AsyncFHIRClient":
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

    def _get_client(self) -> httpx.AsyncClient:
        return self._external_client or self._client or httpx.AsyncClient(timeout=self.timeout)

    async def _aget(self, url: str, params: Optional[Mapping[str, Any]] = None) -> dict:
        """
        Internal GET wrapper that surfaces OperationOutcome details where possible.
        If no managed client is active, it creates a short-lived one.
        """
        owns = False
        client = self._external_client or self._client
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            owns = True
        try:
            resp = await client.get(url, headers=self._accept, params=params)
            if resp.status_code >= 400:
                # Try to surface OperationOutcome
                try:
                    oo = _OperationOutcome.model_validate(resp.json())
                    raise FHIRClientError(f"HTTP {resp.status_code}: OperationOutcome: {oo.issue}")
                except Exception:
                    raise FHIRClientError(f"HTTP {resp.status_code}: {resp.text[:500]}")
            try:
                return resp.json()
            except ValueError as e:
                raise FHIRClientError(f"Non-JSON response from {url}: {e}")
        finally:
            if owns:
                await client.aclose()

    # ---- public API (read-only) -----------------------------------------

    async def get_capability(self) -> dict:
        """GET [base]/metadata → CapabilityStatement"""
        return await self._aget(self._url("metadata"))

    async def read(self, resource_type: str, resource_id: str) -> dict:
        """GET [base]/{type}/{id}"""
        return await self._aget(self._url(resource_type, resource_id))

    async def search(self, resource_type: str, params: Optional[Mapping[str, Any]] = None) -> dict:
        """GET [base]/{type}?<params>  → Bundle"""
        return await self._aget(self._url(resource_type), params=params or {})

    async def count(self, resource_type: str, params: Optional[Mapping[str, Any]] = None) -> int:
        """Efficient count via `_summary=count&_total=accurate`."""
        p = {"_summary": "count", "_total": "accurate", **(params or {})}
        bundle = await self.search(resource_type, p)
        try:
            parsed = _ParsedBundle.model_validate(bundle)
        except ValidationError:
            return int(bundle.get("total", 0) or 0)
        return int(parsed.total or 0)

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

        try:
            parsed = _ParsedBundle.model_validate(bundle)
        except ValidationError:
            entries = bundle.get("entry") or []
            return entries[0].get("resource") if entries else None

        if parsed.entry:
            return parsed.entry[0].get("resource")
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
        url = f"{self.base_url.rstrip('/')}/{resource_type}"
        resp = await self._client.get(
            url,
            params=params,
            headers={"Accept": "application/fhir+json"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        bundle = resp.json()
        if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle":
            return []
        entries = bundle.get("entry") or []
        out = []
        for e in entries:
            r = (e or {}).get("resource")
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