# clients/session_client.py
from __future__ import annotations

import httpx
from typing import Optional, Dict, Any

from src.config import get_settings


class SessionClient:
    """Client for managing Docker container sessions via the middleman service.

    Defaults (mem/cpus/timeout) come from environments/configs/sandbox.yaml via get_settings().
    Routes come from env-derived settings:
      - session_base_url → POST /sessions, GET /sessions/:id
      - vm_base_url      → DELETE /vm/sessions/:id
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        timeout_s: Optional[float] = None,
        client: Optional[httpx.AsyncClient] = None,
    ):
        settings = get_settings()
        # Resolve base URL for session lifecycle
        base_url = base_url or settings.session_base_url
        if not base_url:
            raise ValueError("Missing session base URL (settings.session_base_url is not set)")

        # Derive /vm base from the session base (e.g., http://host:3000/sessions → http://host:3000/vm/sessions)
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/sessions"):
            parent = self.base_url.rsplit("/", 1)[0]
            self.vm_base_url = f"{parent}/vm/sessions"
        else:
            # Fallback: replace first occurrence of /sessions, or append /vm/sessions
            self.vm_base_url = (
                self.base_url.replace("/sessions", "/vm/sessions", 1)
                if "/sessions" in self.base_url
                else f"{self.base_url}/vm/sessions"
            )
            
        # Defaults from config
        self._sandbox_cfg = settings.sandbox
        self.timeout = float(timeout_s) if timeout_s is not None else float(settings.timeout_s)

        self._external_client = client
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "SessionClient":
        if self._external_client is None and self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _apost(self, url: str, json: Optional[dict] = None, headers: Optional[Dict[str, str]] = None) -> dict:
        owns = False
        client = self._external_client or self._client
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            owns = True
        try:
            resp = await client.post(url, json=json, headers=headers)
            resp.raise_for_status()
            return resp.json()
        finally:
            if owns:
                await client.aclose()

    async def _aget(self, url: str) -> dict:
        owns = False
        client = self._external_client or self._client
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            owns = True
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()
        finally:
            if owns:
                await client.aclose()

    async def _adelete(self, url: str) -> dict:
        owns = False
        client = self._external_client or self._client
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            owns = True
        try:
            resp = await client.delete(url)
            resp.raise_for_status()
            # DELETE returns plain text ("deleted") in your VM API; be tolerant
            try:
                return resp.json()
            except Exception:
                return {"status": resp.text}
        finally:
            if owns:
                await client.aclose()

    async def create_session(
        self,
        mem_mb: Optional[int] = None,
        cpus: Optional[float] = None,
        image: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Create a new Docker container session.

        Args:
            mem_mb: Memory limit in MB (defaults to sandbox.default_mem_mb)
            cpus: CPU limit (defaults to sandbox.default_cpus)
            image: Docker image to use (optional; middleman has its own default)
            env: Environment variables (optional)

        Returns:
            Session ID string
        """
        request_data: Dict[str, Any] = {}

        # Use provided values or sandbox defaults
        request_data["mem_mb"] = int(mem_mb) if mem_mb is not None else int(self._sandbox_cfg.default_mem_mb)
        request_data["cpus"] = float(cpus) if cpus is not None else float(self._sandbox_cfg.default_cpus)

        if image is not None:
            request_data["image"] = image
        if env is not None:
            # Convert dict to list of "KEY=VALUE" strings expected by middleman
            request_data["env"] = [f"{k}={v}" for k, v in env.items()]

        result = await self._apost(self.base_url, json=request_data)
        return result.get("session_id", "")

    async def get_session(self, session_id: str) -> Dict[str, Any]:
        """
        Get session information from the Sessions API (includes request logs etc).
        """
        return await self._aget(f"{self.base_url}/{session_id}")

    async def delete_session(self, session_id: str) -> bool:
        """
        Delete a session and its Docker container using the VM API.
        """
        try:
            await self._adelete(f"{self.vm_base_url}/{session_id}")
            return True
        except Exception:
            return False

    # Optional helpers if you want to introspect config from callers:

    @property
    def sandbox_limits(self) -> Dict[str, Any]:
        """
        Expose the sandbox limits from config (useful for higher-level orchestration).
        """
        return {
            "max_concurrent_sessions": self._sandbox_cfg.max_concurrent_sessions,
            "exec_timeout_s": self._sandbox_cfg.exec_timeout_s,
            "default_mem_mb": self._sandbox_cfg.default_mem_mb,
            "default_cpus": self._sandbox_cfg.default_cpus,
        }


# Convenience functions for backward compatibility
async def create_session(
    mem_mb: Optional[int] = None,
    cpus: Optional[float] = None,
    image: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> str:
    async with SessionClient() as client:
        return await client.create_session(mem_mb=mem_mb, cpus=cpus, image=image, env=env)


async def get_session(session_id: str) -> Dict[str, Any]:
    async with SessionClient() as client:
        return await client.get_session(session_id)


async def delete_session(session_id: str) -> bool:
    async with SessionClient() as client:
        return await client.delete_session(session_id)
