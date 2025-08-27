# mcp_server/tools/notepad.py
from __future__ import annotations

from ..mcp_app import mcp  # shared FastMCP instance
from ..utils.notepad_store import write, read, clear


@mcp.tool(
    name="notepad_write",
    description="Store a short piece of text in a scratchpad under `key`.",
)
def notepad_write(key: str, text: str) -> str:
    write(key, text)
    return "ok"


@mcp.tool(
    name="notepad_read",
    description="Read previously stored text from the scratchpad.",
)
def notepad_read(key: str) -> str:
    val = read(key)
    return val or ""


@mcp.tool(
    name="notepad_clear",
    description="Delete an entry from the scratchpad.",
)
def notepad_clear(key: str) -> str:
    clear(key)
    return "cleared"
