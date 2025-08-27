# server.py
from __future__ import annotations

from datetime import datetime
from typing import Optional

import random
from fastmcp import FastMCP
try:
    # Python 3.9+
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

mcp = FastMCP("demo-fastmcp")

# --- demo tools ---

@mcp.tool
def hello(name: str) -> str:
    """Greet the user."""
    return f"hey {name} 👋"

@mcp.tool
def add(a: float, b: float) -> float:
    """Add two numbers."""
    return a + b

@mcp.tool
def random_int(low: int = 0, high: int = 100) -> int:
    """Return a random integer in [low, high]."""
    if low > high:
        low, high = high, low
    return random.randint(low, high)

@mcp.tool
def echo(text: str, uppercase: bool = False) -> str:
    """Echo text; optionally uppercase it."""
    return text.upper() if uppercase else text

@mcp.tool
def time_now(tz: str = "UTC") -> str:
    """Current time as ISO 8601. tz should be an IANA zone like 'UTC' or 'America/New_York'."""
    if ZoneInfo is None:
        return datetime.utcnow().isoformat() + "Z"
    try:
        return datetime.now(ZoneInfo(tz)).isoformat()
    except Exception:
        # fallback to UTC if the TZ is unknown
        return datetime.now(ZoneInfo("UTC")).isoformat()

# --- optional health check for quick curl tests ---
@mcp.custom_route("/health", methods=["GET"])
async def health(_req):
    from starlette.responses import PlainTextResponse
    return PlainTextResponse("OK")

if __name__ == "__main__":
    # Run with Streamable HTTP so you can connect via a localhost URL
    # Endpoint will be: http://127.0.0.1:8000/mcp/
    mcp.run(transport="http", host="127.0.0.1", port=8000, path="/mcp")
