



# mcp_server/tools/shell_exec.py
from __future__ import annotations

from typing import Dict

from ..mcp_app import mcp
from ..config import get_settings
from ..utils.shell_client import client as shell_client

settings = get_settings()

# Pull a per-tool timeout (with a sensible default).
_DEFAULT_TIMEOUT = getattr(settings.limits.get("shell_exec"), "timeout_s", 30) or 30

# ───────────────────────────── shell_exec ─────────────────────────────
# A zero-friction shell tool: requires only a single `command` string.
# The ShellClient singleton auto-creates and caches the Alpine session id,
# so callers never see or pass the session id themselves.
if "shell_exec" in settings.enabled:
    @mcp.tool(
        name="shell_exec",
        description=(
            "Execute a shell command in an isolated Alpine sandbox. The session is created "
            "automatically; just pass the command string.\n\n"
            "Runtime environment:\n"
            "• Shell tools: bash, BusyBox core utilities (sh, ls, cp, mv, grep, sed, awk, find, xargs, tar, gzip, etc.),\n"
            "  curl, jq.\n"
            "• Python: system Python 3 with a global virtualenv at /opt/venv already on PATH (so `python` and `pip` use it).\n"
            "  Packages come from the image's alpine_sandbox/requirements.txt at build time. To see what's available, run:\n"
            "  `pip list` or `python -c \"import pkgutil, sys; print(sorted(m.name for m in pkgutil.iter_modules()))\"`.\n"
            "  (The venv is prebuilt; installing additional packages at runtime may be restricted.)\n"
            "• Resource caps (soft): mem and CPU derived from ALPINE_MEM_MB and ALPINE_CPUS.\n\n"
            "Args:\n"
            "  command (str): The shell command to run (e.g., 'ls -la /', 'python -V').\n"
            "  workdir (str, optional): Working directory inside the container (default: user's home).\n"
            "  timeout_secs (int, optional): Server-side timeout for this execution.\n"
            "  user (str, optional): Run as this user (e.g., 'sandbox').\n\n"
            "Returns: {stdout:str, stderr:str, exit_code:int}"
        ),
    )
    def shell_exec(command: str) -> Dict[str, object]:
        try:
            # Use the singleton ShellClient; it auto-creates and caches a session id.
            return shell_client().exec(command, timeout_secs=_DEFAULT_TIMEOUT)
        except Exception as e:
            return {"error": str(e)}
