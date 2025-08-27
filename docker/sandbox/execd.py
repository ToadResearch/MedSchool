from __future__ import annotations
import os, subprocess
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="sandbox", version="1.0.0")

# This image is used for ephemeral runs. Default: this very image.
SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "medschool-sandbox")
RUNTIME       = os.getenv("SANDBOX_RUNTIME", "")      # e.g. "runsc" for gVisor, if installed
OUTPUT_CAP    = 32_768
DEFAULT_TIMEOUT = int(os.getenv("SANDBOX_TIMEOUT", "7"))

class RunReq(BaseModel):
    code: str = Field(default="", description="Python 3 code to run on stdin (python -)")
    timeout_s: int = Field(default=6, ge=1, le=30)
    mem_mb: int = Field(default=512, ge=64, le=4096)
    cpus: float = Field(default=1.0, ge=0.1, le=2.0)

class RunResp(BaseModel):
    stdout: str
    stderr: str
    exit_code: int

@app.get("/healthz")
def healthz():
    return {"ok": True, "image": SANDBOX_IMAGE, "runtime": RUNTIME or "default"}

@app.post("/run", response_model=RunResp)
def run(req: RunReq):
    cmd = [
        "docker", "run", "--rm", "-i",
        "--network", "none",
        "--pids-limit", "64",
        "--memory", f"{req.mem_mb}m",
        "--cpus", str(req.cpus),
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=64m",
        "--tmpfs", "/home:rw,noexec,nosuid,nodev,size=64m",
        # headless + cache paths + thread caps (play nice with --cpus)
        "-e", "PYTHONUNBUFFERED=1",
        "-e", "MPLBACKEND=Agg",
        "-e", "MPLCONFIGDIR=/tmp",
        "-e", "XDG_CACHE_HOME=/tmp",
        "-e", "OPENBLAS_NUM_THREADS=1",
        "-e", "OMP_NUM_THREADS=1",
        "-e", "NUMEXPR_MAX_THREADS=1",
    ]
    if RUNTIME:
        cmd += ["--runtime", RUNTIME]

    # Run this same image, overriding command to "python -"
    cmd += [SANDBOX_IMAGE, "python", "-"]

    try:
        proc = subprocess.run(
            cmd,
            input=req.code,
            text=True,
            capture_output=True,
            timeout=min(req.timeout_s, DEFAULT_TIMEOUT),
        )
        out = (proc.stdout or "")[:OUTPUT_CAP]
        err = (proc.stderr or "")[:OUTPUT_CAP]
        return RunResp(stdout=out, stderr=err, exit_code=proc.returncode)
    except subprocess.TimeoutExpired:
        return RunResp(stdout="", stderr="TIMEOUT", exit_code=124)
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=f"Docker CLI not found: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
