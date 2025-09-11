# mcp_server/utils/shell_client.py
from __future__ import annotations

"""
Middleman terminal client (one-shot mode, no timers).

Behavior:
  - For each .exec() call:
      1) Create a fresh Alpine sandbox session via POST /sessions
      2) POST the command to /interminal with x-session-id
      3) DELETE /vm/sessions/:id in a finally block
  - No session reuse, no idle timers, no lingering containers.

Optional override:
  - Set SANDBOX_PERSIST=1 (env) to keep the session alive after .exec()
    (useful for ad-hoc debugging). In that case, callers can pass a
    "persist" argument to .exec(persist=True/False) to override the env.

Env vars:
  TERMINAL_PROXY_INTERNAL_BASE / TERMINAL_BASE_URL / TERMINAL_UPSTREAM_BASE
    Base URLs for Middleman terminal/Vm endpoints.
  ALPINE_MEM_MB, ALPINE_CPUS
    Soft caps passed to the session creation API.
  SANDBOX_PERSIST
    "1" to persist sessions after exec; "0" (default) deletes immediately.
"""

import os
from typing import Dict, List, Optional

import httpx


def _terminal_base() -> str:
    return (
        os.getenv("TERMINAL_PROXY_INTERNAL_BASE", "").rstrip("/")
        or os.getenv("TERMINAL_BASE_URL", "").rstrip("/")
        or os.getenv("TERMINAL_UPSTREAM_BASE", "http://middleman:3000/interminal").rstrip("/")
    )

def _sessions_base_from_terminal(terminal_base: str) -> str:
    # e.g. http://middleman:3000/sessions
    import urllib.parse as up
    parts = up.urlsplit(terminal_base)
    return up.urlunsplit((parts.scheme, parts.netloc, "/sessions", "", ""))

def _vm_base_from_terminal(terminal_base: str) -> str:
    # e.g. http://middleman:3000/vm
    import urllib.parse as up
    parts = up.urlsplit(terminal_base)
    return up.urlunsplit((parts.scheme, parts.netloc, "/vm", "", ""))


_ALPINE_MEM_MB = int(float(os.getenv("ALPINE_MEM_MB", "256")))
_ALPINE_CPUS = float(os.getenv("ALPINE_CPUS", "0.5"))
_ENV_PERSIST_DEFAULT = os.getenv("SANDBOX_PERSIST", "0") == "1"


class ShellClient:
    """
    One-shot Middleman terminal client:
      • POST /sessions → create ephemeral session
      • POST {terminal}/ (x-session-id) → exec once
      • DELETE /vm/sessions/:id → always delete unless persist=True
    """

    def __init__(self, timeout_s: int = 120):
        self.timeout_s = timeout_s
        self._terminal_base = _terminal_base()
        self._sessions_base = _sessions_base_from_terminal(self._terminal_base)
        self._vm_base = _vm_base_from_terminal(self._terminal_base)
        self._http = httpx.Client(timeout=self.timeout_s)

    # ---- session lifecycle (private helpers) ----
    def _create_session(self) -> str:
        url = f"{self._sessions_base}/"
        payload = {
            "env": [],
            "mem_mb": _ALPINE_MEM_MB,
            "cpus": _ALPINE_CPUS,
        }
        r = self._http.post(url, json=payload)
        r.raise_for_status()
        data = r.json()
        sid = data.get("session_id")
        if not sid:
            raise RuntimeError(f"Session create returned no session_id: {data}")
        return sid

    def _delete_session(self, sid: Optional[str]) -> None:
        if not sid:
            return
        url = f"{self._vm_base}/sessions/{sid}"
        try:
            r = self._http.delete(url)
            if r.status_code not in (200, 404):
                # Non-fatal: log-y behavior
                print(f"[shell_client] delete_session {sid} -> {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"[shell_client] delete_session error: {e}")

    # ---- public API ----
    def exec(
        self,
        command: str,
        *,
        workdir: Optional[str] = None,
        env: Optional[List[str]] = None,
        timeout_secs: Optional[int] = None,
        user: Optional[str] = None,
        persist: Optional[bool] = None,
    ) -> Dict[str, object]:
        """
        Execute a single command inside a fresh ephemeral sandbox session.

        Args:
          command: Shell command string. Middleman wraps non-shell forms in 'sh -c' for convenience.
          workdir, env, timeout_secs, user: forwarded to Middleman.
          persist: If True, keep the session alive after exec. If False (default), delete it.
                   If None, falls back to SANDBOX_PERSIST env (default False).

        Returns:
          {stdout: str, stderr: str, exit_code: int}
        """
        sid: Optional[str] = None
        keep = _ENV_PERSIST_DEFAULT if persist is None else bool(persist)

        try:
            sid = self._create_session()

            url = f"{self._terminal_base}/"
            payload = {
                "cmd": [command],  # Middleman will wrap in sh -c as needed
                "workdir": workdir,
                "env": env,
                "timeout_secs": timeout_secs if timeout_secs is not None else self.timeout_s,
                "user": user,
            }
            payload = {k: v for k, v in payload.items() if v is not None}

            r = self._http.post(url, headers={"x-session-id": sid}, json=payload)
            if r.status_code == 200:
                data = r.json()
                return {
                    "stdout": data.get("stdout", ""),
                    "stderr": data.get("stderr", ""),
                    "exit_code": int(data.get("exit_code", 0)),
                }

            # non-200 → raise with context
            raise RuntimeError(f"Command failed {r.status_code}: {r.text[:4000]}")

        finally:
            if not keep:
                self._delete_session(sid)


# ---- Singleton factory (optional; mirrors prior usage) ----

_client_singleton: Optional[ShellClient] = None

def client() -> ShellClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = ShellClient()
    return _client_singleton
