# clients/terminal_client.py
from __future__ import annotations

import httpx
from typing import Optional, List, Dict, Any

from src.config import get_settings



class TerminalClient:
    """Client for executing terminal commands in Docker containers via the middleman service"""

    def __init__(self, base_url: Optional[str] = None, timeout_s: float = 30.0, client: Optional[httpx.AsyncClient] = None):
        if base_url is None:
            config = get_settings()
            base_url = config.terminal_base_url
        if not base_url:
            raise ValueError("Missing terminal base URL (check TERMINAL_* env vars)")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_s
        self._external_client = client
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "TerminalClient":
        if self._external_client is None and self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _url(self, *parts: str) -> str:
        return "/".join([self.base_url, *[p.strip("/") for p in parts]])

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

    async def execute_command(
        self,
        session_id: str,
        command: str,
        args: Optional[List[str]] = None,
        working_directory: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        user: Optional[str] = None,
        env: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a terminal command in the specified session.

        Args:
            session_id: The session ID to execute the command in
            command: The command to execute
            args: Optional list of arguments
            working_directory: Optional working directory
            timeout_seconds: Optional timeout in seconds
            user: Optional user to run as
            env: Optional environment variables

        Returns:
            Dict containing stdout, stderr, and exit_code
        """
        # Build the command array
        cmd = [command]
        if args:
            cmd.extend(args)

        # Prepare the request body
        request_data = {
            "cmd": cmd,
        }

        if working_directory:
            request_data["workdir"] = working_directory
        if timeout_seconds:
            request_data["timeout_secs"] = timeout_seconds
        if user:
            request_data["user"] = user
        if env:
            request_data["env"] = env

        # Prepare headers
        headers = {
            "Content-Type": "application/json",
            "x-session-id": session_id,
        }

        try:
            result = await self._apost(self.base_url, json=request_data, headers=headers)
            return {
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
                "exit_code": result.get("exit_code", 0),
                "success": result.get("exit_code", 0) == 0,
            }
        except Exception as e:
            return {
                "stdout": "",
                "stderr": f"Error executing command: {str(e)}",
                "exit_code": 1,
                "success": False,
            }