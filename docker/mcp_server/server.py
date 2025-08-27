# mcp_server/server.py
from __future__ import annotations
import os
from typing import List

from .mcp_app import mcp
from .config import get_settings
from .tools import load as load_tool, ALL as ALL_TOOLS

# Load settings (enabled tools, limits, etc.)
settings = get_settings()

# Dynamically import and register enabled tools.
# Importing a tool module causes its @mcp.tool decorators to run.
_loaded_tools: List[str] = []
for tool_name in settings.enabled:
    if tool_name not in ALL_TOOLS:
        raise RuntimeError(f"Unknown tool {tool_name!r} in tools.yaml (allowed: {sorted(ALL_TOOLS)})")
    load_tool(tool_name)
    _loaded_tools.append(tool_name)

# Simple health check
@mcp.custom_route("/health", methods=["GET"])
async def health(_req):
    from starlette.responses import PlainTextResponse
    return PlainTextResponse("OK")

# Handy root route listing the enabled tools
@mcp.custom_route("/", methods=["GET"])
async def root(_req):
    from starlette.responses import JSONResponse
    return JSONResponse({"name": "medschool-mcp", "tools": sorted(_loaded_tools)})

if __name__ == "__main__":
    # Run with Streamable HTTP so you can connect via a container port
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8000"))
    path = os.getenv("MCP_HTTP_PATH", "/mcp")
    mcp.run(transport="http", host=host, port=port, path=path)
