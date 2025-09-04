# mcp_server/tools/shell_exec.py
from __future__ import annotations

import json
import os
import textwrap
from typing import Any, Optional

import httpx
from fastmcp.exceptions import ToolError
from pydantic import BaseModel

from ..mcp_app import mcp  # shared FastMCP instance

from ..config import get_settings

settings = get_settings()

# Reuse the same executor service and resource caps as python_exec
# prefer canonical url → internal proxy
EXEC_URL = settings.sandbox_base_url
CPU_SEC  = int(os.getenv("SANDBOX_EXEC_TIMEOUT_S", "6"))
MEM_MB   = int(os.getenv("SANDBOX_EXEC_MEM_MB", "512"))
CPUS     = float(os.getenv("SANDBOX_EXEC_CPUS", "1.0"))

class ShellExecResult(BaseModel):
    stdout: str
    stderr: str
    exit_code: int

@mcp.tool(
    name="shell_exec",
    description=(
        "Run a short POSIX shell script in a locked-down sandbox.\n\n"
        "Runtime environment:\n"
        "• Shells: bash (preferred) and sh (/bin/sh).\n"
        "• CLI tools: jq, core POSIX utilities (cat, tee, head, tail, sort, uniq, wc, cut),\n"
        "  grep, sed, awk, find, xargs.\n"
        "• Python: python3.11 available for `python -`; preinstalled libs include numpy, pandas,\n"
        "  matplotlib (Agg), scipy, scikit-learn, rapidfuzz, and python-dateutil.\n"
        "• Networking is disabled; root FS is read-only; /tmp and /home are writable tmpfs.\n"
        "• CPU/memory/time limited; stdout/stderr capped to ~32 KB.\n\n"
        "Typical uses: pipe large JSON through jq, tee results to temp files, or mix shell with `python -`.\n\n"
        "Args:\n"
        "  script (str): Command(s) to execute (runs via bash -lc if available, otherwise sh -lc).\n"
        "  stdin  (str, optional): Data to feed on stdin.\n"
        "  timeout_s (int, optional): CPU time cap (defaults from env).\n"
        "Returns: {stdout, stderr, exit_code} (non-zero exit does not raise).\n"
    ),
)
def shell_exec(script: str, stdin: Optional[str] = None, timeout_s: Optional[int] = None) -> ShellExecResult:
    # Build a tiny Python wrapper that spawns the shell inside the *inner* sandbox container.
    py_wrapper = f"""
import json, subprocess, os, sys
script = {json.dumps(script)}
inp    = {json.dumps(stdin or "")}
# Prefer bash if present for nicer pipes/globs; fall back to sh.
shell = "/bin/bash" if os.path.exists("/bin/bash") else "/bin/sh"
p = subprocess.run([shell, "-lc", script], input=inp, text=True, capture_output=True)
print(json.dumps({{"stdout": p.stdout, "stderr": p.stderr, "exit_code": p.returncode}}))
""".strip()

    payload = {
        "code": textwrap.dedent(py_wrapper),
        "timeout_s": int(timeout_s or CPU_SEC),
        "mem_mb": MEM_MB,
        "cpus": CPUS,
    }

    try:
        with httpx.Client(timeout=(timeout_s or CPU_SEC) + 3) as client:
            resp = client.post(f"{EXEC_URL}/run", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPError as e:
        status = getattr(getattr(e, "response", None), "status_code", None)
        reason = getattr(getattr(e, "response", None), "reason_phrase", "")
        body_snip = ""
        if getattr(e, "response", None) is not None:
            try:
                j = e.response.json()
                body_snip = json.dumps(j)[:1000]
            except Exception:
                body_snip = (e.response.text or "")[:1000]
        raise ToolError(f"sandbox HTTP error: {status} {reason}. Body: {body_snip}. Check sandbox service ({EXEC_URL}).") from e
    except Exception as e:
        raise ToolError(f"sandbox invocation error: {e}.") from e

    # Outer sandbox succeeded; inner shell result is encoded in stdout JSON.
    out = (data.get("stdout") or "").strip()
    err = (data.get("stderr") or "")
    if not out:
        # Non-fatal: shell could be silent; return structured with exit_code if we can parse it
        return ShellExecResult(stdout="", stderr=err, exit_code=0)

    try:
        inner = json.loads(out)
        return ShellExecResult(
            stdout=inner.get("stdout", ""),
            stderr=(inner.get("stderr", "") or err),
            exit_code=int(inner.get("exit_code", 0)),
        )
    except Exception:
        # If the wrapper somehow didn't print JSON, fall back to outer output.
        return ShellExecResult(stdout=out, stderr=err, exit_code=0)
