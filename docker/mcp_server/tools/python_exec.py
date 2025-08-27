# mcp_server/tools/python_exec.py
from __future__ import annotations

import os
import textwrap
import json
from typing import Any

import httpx
from pydantic import BaseModel
from fastmcp.exceptions import ToolError 
from ..mcp_app import mcp  # shared FastMCP instance

# Runtime config (overridable via env)
EXEC_URL = os.getenv("PYEXEC_EXECUTOR_URL", "http://sandbox:8088")
CPU_SEC  = int(os.getenv("PYEXEC_TIMEOUT_S", "6"))
MEM_MB   = int(os.getenv("PYEXEC_MEM_MB", "512"))
CPUS     = float(os.getenv("PYEXEC_CPUS", "1.0"))


class PythonExecResult(BaseModel):
    """
    Structured result returned to the MCP client.
    FastMCP will expose this as `structuredContent` and also include a text JSON fallback.
    """
    stdout: str
    stderr: str
    exit_code: int
    # If the user's code prints JSON to stdout, we'll parse it and place it here.
    json: Any | None = None


# sandbox packages are defined in docker/sandbox/requirements.txt
@mcp.tool(
    name="python_exec",
    description=(
        "Run short Python 3.11 code inside a locked-down sandbox.\n\n"
        "Preinstalled libs: numpy, pandas, matplotlib (Agg), scipy, scikit-learn, "
        "rapidfuzz, python-dateutil. You may not use any other packages.\n\n"
        "Args:\n"
        "  code (str, optional): Python source to execute. "
        "  You should print your final result to stdout (prefer JSON). "
        "  If omitted, returns JSON with Python version and installed packages.\n\n"
        "Returns (structured): {stdout, stderr, exit_code, json} "
        "where `json` is a parsed object if stdout was valid JSON.\n\n"
        "Errors: This tool raises MCP ToolError for sandbox failures or if the script "
        "produces no stdout."
    ),
)
def python_exec(code: str | None = None) -> PythonExecResult:
    # If no code is provided, report environment info (python + installed packages)
    if not code:
        code = (
            "import json,importlib.metadata,platform\n"
            "pkgs=[{'name':d.metadata['Name'],'version':d.version}"
            "      for d in importlib.metadata.distributions()]\n"
            "print(json.dumps({'python':platform.python_version(),"
            "                  'packages':pkgs}))"
        )

    payload = {
        "code": textwrap.dedent(code),
        "timeout_s": CPU_SEC,
        "mem_mb": MEM_MB,
        "cpus": CPUS,
    }

    try:
        # Call the sandbox executor
        with httpx.Client(timeout=CPU_SEC + 3) as client:
            resp = client.post(f"{EXEC_URL}/run", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        # Transport/HTTP problems reaching the sandbox → tool execution error
        raise ToolError(f"sandbox HTTP error: {e}. Check sandbox service ({EXEC_URL}).") from e
    except Exception as e:
        # Any unexpected client-side error
        raise ToolError(f"sandbox invocation error: {e}.") from e

    # Normalize fields from sandbox response
    out = (data.get("stdout") or "")
    err = (data.get("stderr") or "")
    exit_code = int(data.get("exit_code", 1))

    # Non-zero exit from sandbox → propagate as tool error with stderr
    if exit_code != 0:
        # keep message short but informative
        msg = f"sandbox failed (exit {exit_code})"
        if err.strip():
            # include a trimmed stderr snippet to help the user
            snippet = err.strip()
            if len(snippet) > 800:
                snippet = snippet[:800] + "…"
            msg += f": {snippet}"
        raise ToolError(msg)

    # Zero exit but *no* stdout and *no* stderr → treat as an error (avoid silent guessing)
    if not out.strip():
        raise ToolError(
            "No stdout captured. Make sure to print any results you want to use "
            "(prefer JSON, e.g., print(json.dumps({...})))."
        )

    # Try to parse JSON (helpful for downstream agents/clients)
    parsed = None
    try:
        parsed = json.loads(out) if out.strip() else None
    except Exception:
        parsed = None  # stdout wasn't JSON; that's fine

    # Success: return structured content
    return PythonExecResult(stdout=out, stderr=err, exit_code=exit_code, json=parsed)
