# mcp_server/mcp_app.py
from __future__ import annotations
from fastmcp import FastMCP

# Single shared MCP app used by server and all tool modules
mcp = FastMCP("medschool-mcp")