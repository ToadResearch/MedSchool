# environment/src/session_manager.py
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Dict, Optional, Any, List, Callable

import httpx

from .config import get_settings
from .tasks.task_manager import TaskManager, Task
from .clients.fhir_client import FHIRClient
from .clients.session_client import SessionClient
from .clients.terminal_client import TerminalClient
from .tools.tool_manager import ToolManager


@dataclass
class SessionContext:
    """
    Per-session bundle:
      - docker session id
      - bound clients (FHIR + Terminal)
      - optional Task metadata
      - free-form meta
    """
    session_id: str
    task: Optional[Task] = None
    fhir_client: Optional[FHIRClient] = None
    terminal_client: Optional[TerminalClient] = None
    meta: Dict[str, Any] = field(default_factory=dict)


class SessionManager:
    """
    Orchestrates (Task + Docker container + per-session clients).
    Each Docker container == one session with isolated clients.

    Design:
      • One manager-owned SessionClient handles VM/session lifecycle
        (create/destroy). It is NOT injected into container contexts.
      • Each session gets its own FHIR and Terminal client instances,
        all sharing a single httpx.AsyncClient owned by the manager.
      • Concurrency cap defaults to sandbox.yaml (max_concurrent_sessions).
    """

    def __init__(
        self,
        *,
        max_concurrent: int | None = None,
        timeout_s: float | None = None,
        tools_pkg: str = "src.tools.definitions",
        tools_filter: Callable[[str], bool] | None = None,
        task_filepath: Optional[str] = None,
    ):
        config = get_settings()

        # Concurrency cap: derive from sandbox unless explicitly overridden
        self.max_concurrent = int(max_concurrent) if max_concurrent is not None \
            else int(config.sandbox.max_concurrent_sessions)
        if self.max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")

        self._lock = asyncio.Lock()
        self._sessions: Dict[str, SessionContext] = {}

        # Shared timeout + HTTP client (one pool for everything under the manager)
        self._timeout_s = float(timeout_s) if timeout_s is not None else float(config.timeout_s)
        self._http: httpx.AsyncClient = httpx.AsyncClient(timeout=self._timeout_s)

        # Single SessionClient that manages all VM lifecycle operations
        self.sessions_api = SessionClient(timeout_s=self._timeout_s, client=self._http)

        # Optional base URL for per-session FHIR (can be None if unused)
        self._fhir_base_url = config.fhir_base_url

        # Global TaskManager that can feed tasks into sessions
        self.task_manager = TaskManager(task_filepath=task_filepath)

        # Tool registry (reads tools.yaml + modules in tools_pkg)
        def _combined_filter(name: str) -> bool:
            return tools_filter(name) if tools_filter else True

        self.tools = ToolManager(
            session_manager=self,
            definitions_pkg=tools_pkg,
            tool_name_filter=_combined_filter,
        )

    # ---------- Context management / shutdown ----------

    async def __aenter__(self) -> "SessionManager":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Gracefully close shared resources."""
        try:
            await self.stop_all()
        except Exception:
            pass
        try:
            await self._http.aclose()
        except Exception:
            pass
    # ---------- Introspection ----------

    @property
    def active_session_ids(self) -> List[str]:
        return list(self._sessions.keys())

    def get(self, session_id: str) -> Optional[SessionContext]:
        return self._sessions.get(session_id)

    def require_session(self, session_id: str) -> SessionContext:
        """
        Resolve a session or raise with a helpful message.
        Tools should call this to get their per-session clients.
        """
        ctx = self.get(session_id)
        if not ctx:
            raise KeyError(
                f"Session '{session_id}' not found. "
                f"Active: {', '.join(self.active_session_ids) or '(none)'}"
            )
        return ctx

    # ---------- Lifecycle ----------

    async def start_session(
        self,
        *,
        mem_mb: int | None = None,
        cpus: float | None = None,
        image: str | None = None,
        env: Optional[Dict[str, str]] = None,
        task: Optional[Task] = None,
    ) -> SessionContext:
        """
        Create a new Docker container and attach per-session clients.
        Enforces max_concurrent.
        """
        async with self._lock:
            if len(self._sessions) >= self.max_concurrent:
                raise RuntimeError(
                    f"Max concurrent sessions reached ({self.max_concurrent}). "
                    "Stop a session or increase the cap."
                )

            # Container lifecycle via the manager-owned SessionClient
            session_id = await self.sessions_api.create_session(
                mem_mb=mem_mb, cpus=cpus, image=image, env=env
            )

            # Per-session FHIR client (shares manager's httpx client)
            fhir = FHIRClient(
                base_url=self._fhir_base_url,
                timeout_s=self._timeout_s,
                client=self._http,
            )

            # Per-session Terminal client (shares manager's httpx client)
            term = TerminalClient(
                timeout_s=self._timeout_s,
                client=self._http,
            )

            ctx = SessionContext(
                session_id=session_id,
                task=task,
                fhir_client=fhir,
                terminal_client=term,
                meta={"image": image, "mem_mb": mem_mb, "cpus": cpus, "env": dict(env or {})},
            )

            self._sessions[session_id] = ctx
            return ctx

    async def stop_session(self, session_id: str) -> bool:
        """
        Tear down per-session resources and delete the Docker session.
        """
        async with self._lock:
            ctx = self._sessions.pop(session_id, None)

        if not ctx:
            return False

        # Best-effort VM deletion via the manager-owned SessionClient
        try:
            await self.sessions_api.delete_session(session_id)
        except Exception:
            pass

        # Nothing special to close for fhir/terminal because they share the manager's httpx client.
        return True

    async def stop_all(self) -> None:
        for sid in list(self.active_session_ids):
            await self.stop_session(sid)

    # ---------- Rollouts ----------

    async def new_task(
        self,
        *,
        mem_mb: int | None = None,
        cpus: float | None = None,
        image: str | None = None,
        env: Optional[dict[str, str]] = None,
    ) -> SessionContext:
        """Pop the next Task (if any) and start a fresh session bound to it."""
        task = self.task_manager.next_task()
        if task is None:
            raise RuntimeError("No tasks available")
        return await self.start_session(
            mem_mb=mem_mb,
            cpus=cpus,
            image=image,
            env=env,
            task=task,
        )

