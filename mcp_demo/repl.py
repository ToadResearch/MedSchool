# client_repl.py
# modified from https://github.com/willccbb/verifiers/blob/main/verifiers/scripts/eval.py
# and https://github.com/willccbb/verifiers/blob/main/verifiers/envs/environment.py
from __future__ import annotations
from typing import List, Dict, Optional, Union, Generator, Any, Tuple, Callable
from openai import OpenAI
from pathlib import Path

from fastmcp import Client as MCPClient
# NOTE: use transport inference so the client stays compatible with the current MCP spec
# (Streamable HTTP at /mcp, SSE at /sse, headers from config, etc.)
from fastmcp.client.transports import infer_transport

import os
import json
import argparse
import asyncio
import threading
import textwrap
import importlib
from datetime import datetime, timezone
from dotenv import load_dotenv
from dataclasses import is_dataclass, asdict  # for safe JSON conversion of tool results

load_dotenv()

# ===================== CORE CLIENT (UNCHANGED) =====================

def get_client(
        model: str,
        api_key_var: str,
        api_base_url: str,
        endpoints_path: str = "endpoints.py",
    ) -> OpenAI:
    try:
        endpoints_path_obj = Path(endpoints_path)
        if endpoints_path_obj.is_dir():
            endpoints_file = endpoints_path_obj / "endpoints.py"
        else:
            endpoints_file = endpoints_path_obj

        if endpoints_file.exists():
            spec = importlib.util.spec_from_file_location("endpoints", endpoints_file)
            assert spec and spec.loader
            endpoints_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(endpoints_module)
            ENDPOINTS = endpoints_module.ENDPOINTS
        else:
            raise ImportError(f"endpoints.py not found at {endpoints_file}")
    except (ImportError, AttributeError):
        print(
            f"No local endpoint registry found at {endpoints_path}. \
            Please specify the model name (-m), API host base URL (-b), and API key variable name (-k)."
        )
        ENDPOINTS = {}

    if model in ENDPOINTS:
        api_key_var = ENDPOINTS[model]["key"]
        api_base_url = ENDPOINTS[model]["url"]
        model = ENDPOINTS[model]["model"]

    return OpenAI(api_key=os.getenv(api_key_var, "EMPTY"), base_url=api_base_url)


# ===================== TTY HELPERS =====================

BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
MAGENTA = "\033[35m"
BLUE = "\033[34m"   # tag for "tool call>"
GRAY = "\033[90m"   # indented args/result/progress lines (light gray)
# bright variants for clearer section headers
BRIGHT_CYAN = "\033[96m"
BRIGHT_GREEN = "\033[92m"
BRIGHT_BLUE = "\033[94m"
WHITE = "\033[97m"

def info(msg: str): print(f"{DIM}{msg}{RESET}")
def warn(msg: str): print(f"{YELLOW}{msg}{RESET}")

def _rule():
    print(f"{DIM}{'─'*60}{RESET}")

def header(title: str):
    print(f"{MAGENTA}{BOLD}{title}{RESET}")
    _rule()

def banner():
    print(f"{BOLD}Chat REPL ready{RESET} — type your message and press Enter.\n")

    header("Chat Controls")
    print(f"  {CYAN}/system <text>{RESET}          Set or replace the system prompt")
    print(f"  {CYAN}/stream on|off{RESET}          Toggle token streaming (works with tools)")
    print(f"  {CYAN}/reset{RESET}                  Clear conversation (keeps system)")
    print()

    header("MCP Tools & Servers")
    print(f"  {CYAN}/tools on|off{RESET}           Enable/disable MCP tool-calling")
    print(f"  {CYAN}/tools list{RESET}             List active tools (from enabled servers)")
    print(f"  {CYAN}/mcp{RESET}                    List MCP servers and status")
    print(f"  {CYAN}/mcp enable <name|all>{RESET}  Enable/connect server(s)")
    print(f"  {CYAN}/mcp disable <name|all>{RESET} Disable/disconnect server(s)")
    print(f"  {CYAN}/mcp tools <name>{RESET}       List tools for a specific server (enabled)")
    print(f"  {CYAN}/mcp reload{RESET}             Reload mcp.json from disk")
    print()

    header("Files & Persistence")
    print(f"  {CYAN}/save <path>{RESET}            Save transcript to JSON")
    print(f"  {CYAN}/load <path>{RESET}            Load transcript from JSON")
    print()

    header("Exit")
    print(f"  {CYAN}/exit{RESET}                   Quit")
    print()

# ===================== GENERIC HELPERS =====================

def _collapse_messages_to_prompt(messages: List[Dict[str, str]]) -> str:
    """Squash chat messages into a plain prompt for /completions fallback."""
    parts = []
    for m in messages:
        parts.append(f"{m.get('role','user').upper()}: {m.get('content','')}")
    parts.append("ASSISTANT:")
    return "\n".join(parts)

def load_transcript(path: str) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "messages" in data:
        data = data["messages"]
    if not isinstance(data, list):
        raise ValueError("Transcript must be a list or an object with 'messages'.")
    return data

def save_transcript(path: str, messages: List[Dict[str, Any]]):
    # Use timezone-aware UTC
    default_dir = "chat_logs"
    os.makedirs(default_dir, exist_ok=True)
    if not path.startswith("/"):
        path = os.path.join(default_dir, path)
    out = {"created_at": datetime.now(timezone.utc).isoformat(), "messages": messages}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    info(f"Saved {len(messages)} messages to {path}")

# --- JSON SAFETY: convert arbitrary tool results into JSON-safe values ---
def _to_jsonable(obj: Any, _depth: int = 0) -> Any:
    """
    Convert common Python objects (dataclasses, pydantic models, bytes, sets, custom objects)
    into JSON-serializable forms. Keeps your transcript/tool messages stable.
    """
    if _depth > 6:
        return str(obj)
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, bytes):
        try:
            return obj.decode("utf-8")
        except Exception:
            return obj.decode("utf-8", errors="replace")
    if is_dataclass(obj):
        try:
            return {k: _to_jsonable(v, _depth + 1) for k, v in asdict(obj).items()}
        except Exception:
            return str(obj)
    # Pydantic v2
    if hasattr(obj, "model_dump") and callable(getattr(obj, "model_dump")):
        try:
            return {k: _to_jsonable(v, _depth + 1) for k, v in obj.model_dump().items()}
        except Exception:
            return str(obj)
    # Pydantic v1
    if hasattr(obj, "dict") and callable(getattr(obj, "dict")):
        try:
            return {k: _to_jsonable(v, _depth + 1) for k, v in obj.dict().items()}
        except Exception:
            return str(obj)
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(v, _depth + 1) for v in obj]
    if hasattr(obj, "__dict__"):
        try:
            return {k: _to_jsonable(v, _depth + 1) for k, v in vars(obj).items()}
        except Exception:
            return str(obj)
    return str(obj)


# ===================== MCP (FastMCP) INTEGRATION =====================

def _run_coro_in_thread(coro):
    """Run an async coroutine from sync code by spinning up a short-lived loop in a thread."""
    result: Dict[str, Any] = {}
    error: Optional[BaseException] = None
    def _runner():
        nonlocal result, error
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as e:
            error = e
    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join()
    if error: raise error
    return result.get("value")

def _sanitize_name(name: str) -> str:
    out = []
    for ch in name:
        if ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9") or ch in {"_", "-"}:
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)

class MCPManager:
    """
    Manages multiple FastMCP servers from mcp.json:
      {
        "mcpServers": {
          "medschool-mcp": { "url": "http://127.0.0.1:8000/mcp" },
          ...
        }
      }
    Allows enabling/disabling servers, listing tools, and converting to OpenAI tool schema.

    NOTE: This version uses transport inference instead of hard-coding Streamable HTTP.
    That keeps the client compatible with the latest MCP transports (Streamable HTTP at
    /mcp, SSE at /sse) and allows headers from mcp.json to be honored.

    NEW: Supports live streaming of tool progress/log events (when server runs over HTTP).
    """
    def __init__(self, config_path: Optional[str] = None):
        self._config_path = config_path or str(Path(__file__).with_name("mcp.json"))
        self._raw_config: Dict[str, Any] = {}
        self._servers: Dict[str, Dict[str, Any]] = {}        # name -> {url, ...}
        self._active: Dict[str, MCPClient] = {}              # name -> MCPClient
        self._tool_cache: Dict[str, List[Dict[str, Any]]] = {}  # name -> tool list objects
        self._fnmap: Dict[str, Tuple[str, str]] = {}         # openai_func_name -> (server, tool)

        # optional CLI printers for streaming events (wired in run_repl)
        self._progress_printer: Optional[Callable[[str], None]] = None
        self._log_printer: Optional[Callable[[str], None]] = None

        self.reload_config()

    # ---------- event printer wiring (for streaming) ----------
    def set_event_printers(
        self,
        progress_printer: Optional[Callable[[str], None]],
        log_printer: Optional[Callable[[str], None]],
    ):
        """
        Set callables that print progress/log lines for any enabled server.
        The handlers are tolerant to different FastMCP versions/signatures.
        """
        self._progress_printer = progress_printer
        self._log_printer = log_printer

    async def _progress_handler(self, *args, **kwargs):
        """
        Normalize FastMCP progress callbacks:
        - new style: (progress, total, message)
        - event style: (event,) where event.data has progress/total/message
        """
        if not self._progress_printer:
            return
        try:
            progress = None
            total = None
            message = None
            if len(args) == 3:
                progress, total, message = args
            elif args:
                ev = args[0]
                data = getattr(ev, "data", None) or {}
                progress = data.get("progress")
                total = data.get("total")
                message = data.get("message") or data.get("msg")
            else:
                progress = kwargs.get("progress")
                total = kwargs.get("total")
                message = kwargs.get("message") or kwargs.get("msg")

            pct = None
            if isinstance(progress, (int, float)) and isinstance(total, (int, float)) and total:
                pct = f"{(float(progress)/float(total))*100:0.1f}%"
            elif isinstance(progress, (int, float)):
                pct = str(progress)

            line = f"progress: {pct if pct is not None else '?'}"
            if message:
                line += f"  {message}"
            self._progress_printer(line)
        except Exception as e:
            self._progress_printer(f"progress: <unparseable> ({e})")

    async def _log_handler(self, *args, **kwargs):
        """Normalize FastMCP log callbacks to a single printable line."""
        if not self._log_printer:
            return
        try:
            msg = None
            extra = None
            if args:
                ev = args[0]
                data = getattr(ev, "data", None) or {}
                msg = data.get("msg") or data.get("message")
                extra = data.get("extra")
            msg = msg or kwargs.get("msg") or kwargs.get("message") or "<log>"
            line = f"log: {msg}"
            if extra is not None:
                try:
                    line += f"  {json.dumps(extra, ensure_ascii=False)}"
                except Exception:
                    line += "  <extra unprintable>"
            self._log_printer(line)
        except Exception as e:
            self._log_printer(f"log: <unparseable> ({e})")

    # ---------- config ----------
    def reload_config(self):
        try:
            with open(self._config_path, "r", encoding="utf-8") as f:
                self._raw_config = json.load(f)
        except FileNotFoundError:
            self._raw_config = {}
        except Exception as e:
            warn(f"Failed to read {self._config_path}: {e}")
            self._raw_config = {}

        self._servers = (self._raw_config or {}).get("mcpServers", {}) or {}

    def list_servers(self) -> List[Tuple[str, str]]:
        """Returns [(name, status)] where status is 'enabled' or 'disabled'."""
        out = []
        for name in sorted(self._servers.keys()):
            out.append((name, "enabled" if name in self._active else "disabled"))
        return out

    # ---------- server lifecycle ----------
    def enable_server(self, name: str) -> str:
        if name == "all":
            failures = []
            for s in list(self._servers.keys()):
                try:
                    self.enable_server(s)
                except Exception as e:
                    failures.append(f"{s}: {e}")
            if failures:
                return "Some servers failed: " + "; ".join(failures)
            return "All servers enabled."
        if name not in self._servers:
            raise KeyError(f"Unknown server '{name}'.")
        if name in self._active:
            return f"Server '{name}' already enabled."

        server_cfg = dict(self._servers[name])  # may include headers or other fields
        url = str(server_cfg.get("url", "")).rstrip("/")
        if not url:
            raise ValueError(f"Server '{name}' missing 'url' in mcp.json.")

        # Use FastMCP's transport inference to pick Streamable HTTP vs SSE and apply headers.
        try:
            if set(server_cfg.keys()) == {"url"}:
                transport = infer_transport(url)
            else:
                transport = infer_transport({"mcpServers": {name: server_cfg}})
            client = MCPClient(
                transport,
                # NEW: wire streaming handlers (work for HTTP transport; safe no-ops for others)
                progress_handler=self._progress_handler,
                log_handler=self._log_handler,
            )
            # test-connect by listing tools (fail fast)
            tools = _run_coro_in_thread(self._list_tools_async(client))
        except Exception:
            # graceful fallback: if URL isn't already /sse, try the SSE mount
            if not url.endswith("/sse"):
                transport = infer_transport(url + "/sse")
                client = MCPClient(
                    transport,
                    progress_handler=self._progress_handler,
                    log_handler=self._log_handler,
                )
                tools = _run_coro_in_thread(self._list_tools_async(client))
            else:
                raise

        self._active[name] = client
        self._tool_cache[name] = tools or []
        # refresh mapping
        self._rebuild_fnmap()
        return f"Enabled '{name}' with {len(self._tool_cache[name])} tool(s)."

    def disable_server(self, name: str) -> str:
        if name == "all":
            names = list(self._active.keys())
            for n in names:
                try: self.disable_server(n)
                except Exception: pass
            return "All servers disabled."
        if name not in self._active:
            return f"Server '{name}' already disabled."
        # attempt graceful close
        try:
            client = self._active[name]
            _run_coro_in_thread(self._close_client_async(client))
        except Exception:
            pass
        finally:
            self._active.pop(name, None)
            self._tool_cache.pop(name, None)
            self._rebuild_fnmap()
        return f"Disabled '{name}'."

    async def _close_client_async(self, client: "MCPClient"):
        try:
            async with client:
                return
        except Exception:
            return

    async def _list_tools_async(self, client: "MCPClient"):
        async with client:
            return await client.list_tools()

    # ---------- tools ----------
    def _rebuild_fnmap(self):
        self._fnmap.clear()
        for server, tools in self._tool_cache.items():
            for t in tools or []:
                tname = getattr(t, "name", None) or getattr(t, "tool", None) or t.get("name")
                if not tname: continue
                # Compose a safe function name: <server>__<tool>
                fname_raw = f"{server}__{tname}"
                fname = _sanitize_name(fname_raw)
                # Avoid collisions by suffixing
                dedup = fname
                i = 2
                while dedup in self._fnmap:
                    dedup = f"{fname[:58]}_{i}"  # keep <64 chars
                    i += 1
                self._fnmap[dedup] = (server, tname)

    def list_active_tools(self) -> List[Tuple[str, str]]:
        """Returns [(server, tool_name)] for currently enabled servers."""
        out: List[Tuple[Tuple[str, str], Tuple[str, str]]] = []
        out = []
        for s, tools in self._tool_cache.items():
            for t in tools or []:
                name = getattr(t, "name", None) or getattr(t, "tool", None) or t.get("name")
                if name: out.append((s, name))
        return out

    def list_server_tools(self, server: str) -> List[str]:
        if server not in self._active:
            return []
        tools = self._tool_cache.get(server, []) or []
        out = []
        for t in tools:
            name = getattr(t, "name", None) or getattr(t, "tool", None) or t.get("name")
            if name: out.append(name)
        return out

    def openai_tools_payload(self) -> List[Dict[str, Any]]:
        """
        Convert active tools to OpenAI function-calling schema.
        Names are namespaced by server using '__' and sanitized.
        """
        payload: List[Dict[str, Any]] = []
        for fname, (server, tool) in self._fnmap.items():
            # Look up the tool schema/desc from cache
            tdesc = None
            for t in self._tool_cache.get(server, []) or []:
                tname = getattr(t, "name", None) or getattr(t, "tool", None) or t.get("name")
                if tname == tool:
                    tdesc = t
                    break
            if tdesc is None:
                continue
            desc = getattr(tdesc, "description", None) or tdesc.get("description", "")
            schema = (
                getattr(tdesc, "input_schema", None)
                or getattr(tdesc, "inputSchema", None)
                or {"type": "object", "properties": {}}
            )
            payload.append({
                "type": "function",
                "function": {
                    "name": fname,
                    "description": desc or "",
                    "parameters": schema,
                }
            })
        return payload

    def call_tool(self, openai_func_name: str, args: Dict[str, Any]) -> Any:
        """Route an OpenAI tool call (namespaced function) to the right MCP server/tool."""
        if openai_func_name not in self._fnmap:
            return {"error": f"Unknown tool '{openai_func_name}'."}
        server, tool = self._fnmap[openai_func_name]
        client = self._active.get(server)
        if client is None:
            return {"error": f"Server '{server}' is disabled."}

        async def _call():
            async with client:
                return await client.call_tool(tool, args)

        res = _run_coro_in_thread(_call())
        # Normalize common FastMCP result shapes, then coerce to JSON-safe
        if hasattr(res, "data"):
            res = res.data
        elif hasattr(res, "text"):
            res = res.text
        elif hasattr(res, "content"):
            res = res.content
        return _to_jsonable(res)


# ===================== CORE MODEL CALLER =====================

def get_model_response(
    *,
    client: OpenAI,
    model: str,
    prompt: Optional[str] = None,
    messages: Optional[List[Dict[str, str]]] = None,
    stream: bool = False,
    request_timeout: Optional[float] = None,
    # MCP options:
    mcp_manager: Optional[MCPManager] = None,
    enable_mcp_tools: bool = False,
    max_tool_rounds: int = 2,
    # CLI tool-call printing (clean single tag + indented, colorized sections)
    tool_print_tag: Optional[Callable[[str], None]] = None,
    tool_print_sub: Optional[Callable[[str], None]] = None,
    **kwargs: Any,
) -> Union[
    str,
    Tuple[str, List[Dict[str, Any]]],
    Generator[str, None, None]
]:
    """
    Call an OpenAI-compatible API using the provided client.
    - Prefers /v1/chat/completions
    - Falls back to /v1/completions
    - Supports streaming via generator when stream=True
    - Optional MCP integration:
        * We keep the assistant call non-streaming when tools are involved (simpler + robust),
          but tools themselves stream progress/logs live via FastMCP handlers wired above.
        * If you want assistant token streaming + tool calling, you'd need to accumulate
          streamed tool_calls deltas; out of scope for this minimal REPL.
    - When tools are enabled, returns (assistant_text, added_messages) where added_messages
      are OpenAI-compatible tool call messages (ONE assistant message containing the full
      tool_calls array, followed by one tool message per call). This makes /save logs
      replayable across OpenAI-compatible systems.
    - If `tool_print_tag`/`tool_print_sub` are provided, prints a single
      "tool call> <tool_name>" tag line per call, plus colorized ARGS/RESULT sections.
    """
    if messages is None and not prompt:
        raise ValueError("Provide either `messages` or `prompt`.")
    if messages is None:
        messages = [{"role": "user", "content": prompt}]  # type: ignore[list-item]

    tools_payload: Optional[List[Dict[str, Any]]] = None
    if enable_mcp_tools and mcp_manager is not None:
        # Allow stream=True; we'll still run the non-stream tool loop for assistant,
        # but FastMCP tool progress/logs will stream through the registered handlers.
        tools_payload = mcp_manager.openai_tools_payload()

    def _stream_chat() -> Generator[str, None, None]:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,    # type: ignore[arg-type]
                stream=True,
                timeout=request_timeout,
                **kwargs,
            )
            for chunk in resp:
                try:
                    delta = chunk.choices[0].delta
                    piece = getattr(delta, "content", None)
                    if piece:
                        yield piece
                except Exception:
                    piece = getattr(chunk.choices[0], "text", "")
                    if piece:
                        yield piece
        except Exception:
            comp = client.completions.create(
                model=model,
                prompt=_collapse_messages_to_prompt(messages),
                stream=True,
                timeout=request_timeout,
                **kwargs,
            )
            for chunk in comp:
                piece = getattr(chunk.choices[0], "text", "")
                if piece:
                    yield piece

    def _tool_loop_nonstream() -> Tuple[str, List[Dict[str, Any]]]:
        local_msgs = [*messages]
        added_msgs: List[Dict[str, Any]] = []  # <- interleavable tool messages for saving
        rounds = 0

        def _shorten(s: str, lim: int = 800) -> str:
            return s if len(s) <= lim else (s[:lim] + "…")

        while True:
            resp = client.chat.completions.create(
                model=model,
                messages=local_msgs,  # type: ignore[arg-type]
                stream=False,
                timeout=request_timeout,
                **({} if not tools_payload else {"tools": tools_payload, "tool_choice": "auto"}),
                **kwargs,
            )
            choice = resp.choices[0]
            msg = getattr(choice, "message", None)

            tool_calls = getattr(msg, "tool_calls", None) if msg else None
            if tool_calls and rounds < max_tool_rounds and mcp_manager is not None:
                rounds += 1

                # Build ONE assistant message that preserves the entire tool_calls array.
                tool_calls_payload = []
                exec_plan = []  # [(name, parsed_args, tool_call_id, argstr_for_print)]
                for call in tool_calls:
                    fn = getattr(call, "function", None)
                    name = getattr(fn, "name", None) if fn else None
                    argstr = getattr(fn, "arguments", "{}") if fn else "{}"
                    call_id = getattr(call, "id", f"toolcall_{rounds}_{len(tool_calls_payload)+1}")
                    call_type = getattr(call, "type", "function")
                    # For execution we need parsed args:
                    try:
                        parsed_args = json.loads(argstr) if isinstance(argstr, str) else (argstr or {})
                    except Exception:
                        parsed_args = {}
                    tool_calls_payload.append({
                        "id": call_id,
                        "type": call_type,
                        "function": {"name": name, "arguments": argstr if isinstance(argstr, str) else json.dumps(argstr)},
                    })
                    exec_plan.append((name, parsed_args, call_id, argstr if isinstance(argstr, str) else json.dumps(argstr)))

                assistant_tool_msg = {
                    "role": "assistant",
                    "content": getattr(msg, "content", None),
                    "tool_calls": tool_calls_payload,
                }
                local_msgs.append(assistant_tool_msg)
                added_msgs.append(assistant_tool_msg)

                # Execute each tool call, print cleanly, and append a tool message per call.
                for name, args_parsed, call_id, argstr_for_print in exec_plan:
                    if tool_print_tag:
                        tool_print_tag(name or "<unknown>")
                    if tool_print_sub:
                        try:
                            # Pass the raw JSON so our pretty-printer can render it nicely.
                            tool_print_sub(f"args: {argstr_for_print}")
                        except Exception:
                            tool_print_sub("args: <unprintable>")

                    try:
                        result = mcp_manager.call_tool(name, args_parsed)  # JSON-safe
                    except Exception as e:
                        result = {"error": f"Tool execution failed: {e}"}

                    tool_reply_msg = {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "name": name,
                        "content": json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result,
                    }
                    local_msgs.append(tool_reply_msg)
                    added_msgs.append(tool_reply_msg)

                    if tool_print_sub:
                        try:
                            rendered = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
                            # Again, don't shorten; let pretty-printer handle truncation gracefully.
                            tool_print_sub(f"result: {rendered}")
                        except Exception:
                            tool_print_sub("result: <unprintable>")
                continue

            # Return assistant's final content and the tool-call messages to persist
            if msg and getattr(msg, "content", None):
                return msg.content, added_msgs
            return (getattr(choice, "text", "") or "", added_msgs)

    def _nonstream_chat_plain() -> str:
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                stream=False,
                timeout=request_timeout,
                **kwargs,
            )
        except Exception:
            comp = client.completions.create(
                model=model,
                prompt=_collapse_messages_to_prompt(messages),
                stream=False,
                timeout=request_timeout,
                **kwargs,
            )
            return comp.choices[0].text or ""
        choice = resp.choices[0]
        msg = getattr(choice, "message", None)
        if msg and getattr(msg, "content", None):
            return msg.content
        return getattr(choice, "text", "") or ""

    if tools_payload:
        return _tool_loop_nonstream()
    return _stream_chat() if stream else _nonstream_chat_plain()


# ===================== REPL =====================

def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="OpenAI-compatible chat REPL with per-server FastMCP tools.")
    here = Path(__file__).parent
    default_endpoints = str(here / "configs" / "endpoints.py")
    default_mcp = str(here / "configs" / "mcp.json")
    p.add_argument("-m", "--model", required=True, help="Model alias or provider model name (endpoints.py supported).")
    p.add_argument("-k", "--api-key-var", default="OPENAI_API_KEY", help="Env var name for API key.")
    p.add_argument("-b", "--base-url", default="https://api.openai.com/v1", help="Base URL for the API host.")
    p.add_argument("-e", "--endpoints-path", default=default_endpoints, help="Path to endpoints.py or a directory containing it.")
    p.add_argument("--timeout", type=float, default=None, help="Per-request timeout (seconds).")
    # NOTE: streaming is ON by default now (unless tools are enabled at startup).
    p.add_argument("--stream", action="store_true", help="(Deprecated) Streaming is enabled by default unless tools are enabled.")
    p.add_argument("--mcp-config", default=default_mcp, help="Optional path to an MCP JSON config. Defaults to ./mcp.json next to client.py")
    p.add_argument("--tools", action="store_true", help="Enable function-calling with MCP tools by default.")
    p.add_argument("--max-tool-rounds", type=int, default=2, help="Max tool-call turns per user message.")
    p.add_argument("--system", default=None, help="Initial system prompt.")
    p.add_argument("--temperature", type=float, default=0.7, help="Sampling temperature.")
    p.add_argument("--max-tokens", type=int, default=512, help="Max tokens for responses.")
    return p

def parse_mcp_path(arg: Optional[str]) -> Optional[str]:
    if arg: return arg
    # default: ./mcp.json next to this file
    return str(Path(__file__).with_name("mcp.json"))

def print_state(model: str, stream_enabled: bool, tools_enabled: bool, mgr: Optional[MCPManager]):
    s = f"{DIM}State — model:{RESET} {BOLD}{model}{RESET}  "
    s += f"{DIM}stream:{RESET} {'on' if stream_enabled else 'off'}  "
    s += f"{DIM}tools:{RESET} {'on' if tools_enabled else 'off'}"
    if tools_enabled and (mgr is not None):
        enabled = len(mgr._active)
        total = len(mgr._servers)
        s += f"  {DIM}[servers enabled:{RESET} {enabled}/{total}{DIM}]{RESET}"
    print(s)

def run_repl(
    client: OpenAI,
    model: str,
    *,
    system_prompt: Optional[str],
    stream_default: bool,
    mcp_manager: MCPManager,
    tools_default: bool,
    max_tool_rounds: int,
    timeout: Optional[float],
    temperature: float,
    max_tokens: int,
):
    messages: List[Dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    # Streaming is ON by default unless tools are enabled at startup.
    stream_enabled = bool(not tools_default) if stream_default is None else bool(stream_default and not tools_default)
    tools_enabled = bool(tools_default)

    # --- small helpers to advertise tool capabilities to the LLM ---
    def _compose_tool_capabilities(mgr: MCPManager) -> str:
        lines = ["You have access to MCP tools. Use them when helpful. Available tools:"]
        for s, t in mgr.list_active_tools():
            lines.append(f"- {s} :: {t}")
        lines.append("When a user requests an action a tool can perform, call the tool.")
        return "\n".join(lines)

    def _upsert_tools_manifest(msgs: List[Dict[str, Any]], text: str):
        TAG = "[[TOOL CAPABILITIES]]"
        payload = TAG + "\n" + text
        # If we already inserted a manifest, update it; otherwise add one near the top.
        for m in msgs:
            if m.get("role") == "system" and isinstance(m.get("content"), str) and m["content"].startswith(TAG):
                m["content"] = payload
                return
        if msgs and msgs[0].get("role") == "system" and not msgs[0]["content"].startswith(TAG):
            msgs.insert(1, {"role": "system", "content": payload})
        else:
            msgs.insert(0, {"role": "system", "content": payload})

    # tool-call printers: one tag line, then indented, colorized sublines (ARGS/RESULT)
    def _print_tool_tag(name: str):
        # Add a little spacing before each new tool section; style the tag distinctly.
        print()
        print(f"{BRIGHT_BLUE}{BOLD}tool call>{RESET} {BOLD}{name}{RESET}")

    # ---------- pretty printing helpers for tool sections ----------
    def _maybe_pretty_json(raw: str) -> str:
        """
        Try to pretty-print JSON; otherwise return the original text.
        """
        s = raw.strip()
        try:
            obj = json.loads(s)
            return json.dumps(obj, ensure_ascii=False, indent=2)
        except Exception:
            return s

    def _truncate_block(text: str) -> str:
        """
        Abbreviate long content for TTY readability.
        - Caps total chars, total lines, and per-line length.
        - Appends a friendly note when truncation happens.
        """
        MAX_CHARS_TOTAL = 6000
        MAX_LINES = 60
        MAX_LINE_LEN = 160

        truncated = False

        if len(text) > MAX_CHARS_TOTAL:
            text = text[:MAX_CHARS_TOTAL]
            truncated = True

        lines = text.splitlines()
        if len(lines) > MAX_LINES:
            lines = lines[:MAX_LINES]
            truncated = True

        clipped: List[str] = []
        for ln in lines:
            if len(ln) > MAX_LINE_LEN:
                clipped.append(ln[:MAX_LINE_LEN] + "…")
                truncated = True
            else:
                clipped.append(ln)

        out = "\n".join(clipped)
        if truncated:
            out += "\n…(output truncated for readability)…"
        return out

    def _indent_block(text: str, n: int = 2) -> str:
        return textwrap.indent(text, " " * n)

    def _rule_light(width: int = 60) -> str:
        return "─" * width

    def _print_tool_sub(line: str):
        """
        Render two structured lines ("args: …" and "result: …") as colorized,
        clearly separated blocks with pretty-printed JSON. Content is light gray
        (to match prior styling) and is truncated to avoid flooding the screen.
        Other lines (progress/log) remain subtle and gray.
        """
        lower = line.lower()
        if lower.startswith("args:"):
            content = line.split(":", 1)[1]
            header = f"{BRIGHT_CYAN}{BOLD}ARGS{RESET}"
            body = _truncate_block(_maybe_pretty_json(content))
            print(f"{BRIGHT_CYAN}{_rule_light()}{RESET}")
            print(header)
            print(f"{BRIGHT_CYAN}{_rule_light()}{RESET}")
            print(f"{GRAY}{_indent_block(body, 2)}{RESET}")
            print()
            return
        if lower.startswith("result:"):
            content = line.split(":", 1)[1]
            header = f"{BRIGHT_GREEN}{BOLD}RESULT{RESET}"
            body = _truncate_block(_maybe_pretty_json(content))
            print(f"{BRIGHT_GREEN}{_rule_light()}{RESET}")
            print(header)
            print(f"{BRIGHT_GREEN}{_rule_light()}{RESET}")
            print(f"{GRAY}{_indent_block(body, 2)}{RESET}")
            print()
            return
        # Fallback (progress/log or unknown): keep it subtle
        print(f"{GRAY}    {line}{RESET}")

    # NEW: wire MCP streaming event printers (progress/log) so they show live during tool runs
    def _mcp_progress_printer(line: str):
        print(f"{GRAY}    {line}{RESET}")

    def _mcp_log_printer(line: str):
        print(f"{GRAY}    {line}{RESET}")

    # Make sure manager knows how to surface streaming events in our CLI
    mcp_manager.set_event_printers(_mcp_progress_printer, _mcp_log_printer)

    banner()
    print_state(model, stream_enabled, tools_enabled, mcp_manager)

    while True:
        try:
            user_input = input(f"{GREEN}you>{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue

        # Commands
        if user_input.startswith("/"):
            cmd, *rest = user_input[1:].split(" ", 1)
            arg = (rest[0].strip() if rest else "")

            if cmd == "help":
                banner()
                continue

            if cmd == "system":
                if not arg:
                    warn("Usage: /system <text>")
                    continue
                if messages and messages[0].get("role") == "system":
                    messages[0]["content"] = arg
                else:
                    messages.insert(0, {"role": "system", "content": arg})
                info("System prompt updated.")
                continue

            if cmd == "stream":
                v = arg.lower()
                if v not in {"on", "off"}:
                    warn("Usage: /stream on|off")
                    continue
                stream_enabled = (v == "on")
                print_state(model, stream_enabled, tools_enabled, mcp_manager)
                continue

            if cmd == "tools":
                if arg.lower() == "list":
                    items = mcp_manager.list_active_tools()
                    if not items:
                        info("No active tools. Enable a server or run /mcp to see options.")
                    else:
                        print(f"{DIM}Active tools:{RESET}")
                        for s, tname in items:
                            print(f"  - {s} :: {tname}")
                    continue

                v = arg.lower()
                if v not in {"on", "off"}:
                    warn("Usage: /tools on|off  (or /tools list)")
                    continue
                tools_enabled = (v == "on")
                if tools_enabled:
                    # Keep the LLM informed of available tools
                    # _upsert_tools_manifest(messages, _compose_tool_capabilities(mcp_manager))
                    info("Tool calling enabled (tool progress/log will stream if supported by server).")
                print_state(model, stream_enabled, tools_enabled, mcp_manager)
                continue

            if cmd == "mcp":
                sub, arg2 = (arg.split(" ", 1) + [""])[:2] if arg else ("", "")
                if sub == "":
                    items = mcp_manager.list_servers()
                    if not items:
                        info("No servers found in mcp.json.")
                    else:
                        print(f"{DIM}Servers:{RESET}")
                        for name, status in items:
                            print(f"  - {name:20s} {status}")
                    continue

                sub = sub.lower()
                if sub == "enable":
                    if not arg2:
                        warn("Usage: /mcp enable <name|all>")
                        continue
                    try:
                        msg = mcp_manager.enable_server(arg2)
                        info(msg)
                        # Auto-enable tool-calling as soon as a server is enabled.
                        if not tools_enabled:
                            tools_enabled = True
                            # inform model about capabilities
                            # _upsert_tools_manifest(messages, _compose_tool_capabilities(mcp_manager))
                        print_state(model, stream_enabled, tools_enabled, mcp_manager)
                        info("MCP server streaming active (progress/log).")
                    except Exception as e:
                        warn(f"Enable failed: {e}")
                    continue

                if sub == "disable":
                    if not arg2:
                        warn("Usage: /mcp disable <name|all>")
                        continue
                    try:
                        msg = mcp_manager.disable_server(arg2)
                        info(msg)
                    except Exception as e:
                        warn(f"Disable failed: {e}")
                    continue

                if sub == "tools":
                    if not arg2:
                        warn("Usage: /mcp tools <name>")
                        continue
                    tl = mcp_manager.list_server_tools(arg2)
                    if not tl:
                        info("No tools (server may be disabled, or no tools available).")
                    else:
                        print(f"{DIM}Tools for {arg2}:{RESET}")
                        for tname in tl:
                            print(f"  - {tname}")
                    continue

                if sub == "reload":
                    mcp_manager.reload_config()
                    info("Reloaded mcp.json.")
                    continue

                warn("Usage: /mcp [enable|disable|tools|reload]")
                continue

            if cmd == "reset":
                sys_msg = messages[0] if (messages and messages[0].get("role") == "system") else None
                messages = []
                if sys_msg:
                    messages.append(sys_msg)
                info("Conversation cleared (system prompt preserved).")
                continue

            if cmd == "save":
                if not arg:
                    warn("Usage: /save ./chat.json")
                    continue
                save_transcript(arg, messages)
                continue

            if cmd == "load":
                if not arg:
                    warn("Usage: /load ./chat.json")
                    continue
                try:
                    data = load_transcript(arg)
                    messages = data
                    info(f"Loaded transcript with {len(messages)} messages.")
                except Exception as e:
                    warn(f"Failed to load transcript: {e}")
                continue

            if cmd == "exit":
                break

            warn("Unknown command. Type /help for options.")
            continue

        # Normal user turn
        messages.append({"role": "user", "content": user_input})

        kwargs = {
            "client": client,
            "model": model,
            "messages": messages,
            "request_timeout": timeout,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if tools_enabled:
            # Tool-calling path: assistant call is non-streaming for simplicity,
            # but MCP tool progress/log will stream live via the wired handlers.
            kwargs.update({
                "enable_mcp_tools": True,
                "mcp_manager": mcp_manager,
                "max_tool_rounds": max_tool_rounds,
                "stream": False,  # assistant non-stream; tool streams via FastMCP
                # print tool calls with a single tag + colorized ARGS/RESULT sections
                "tool_print_tag": _print_tool_tag,
                "tool_print_sub": _print_tool_sub,
            })
            try:
                result = get_model_response(**kwargs)  # returns (text, added_msgs)
                if isinstance(result, tuple):
                    text, added_msgs = result
                    # Persist OpenAI-compatible tool-call messages interleaved in the transcript
                    messages.extend(added_msgs)
                else:
                    text = result  # fallback (shouldn't happen when tools are enabled)
                # Now print and append the assistant's natural-language reply
                print(f"{MAGENTA}assistant>{RESET} {text}")
                messages.append({"role": "assistant", "content": text})
            except Exception as e:
                print(f"{YELLOW}[error]{RESET} {e}")
        else:
            # No tools: do true token streaming from the model if enabled.
            kwargs["stream"] = stream_enabled
            if stream_enabled:
                print(f"{MAGENTA}assistant>{RESET} ", end="", flush=True)
                try:
                    buf = []
                    for chunk in get_model_response(**kwargs):  # generator
                        buf.append(chunk)
                        print(chunk, end="", flush=True)
                    print()
                    messages.append({"role": "assistant", "content": "".join(buf)})
                except Exception as e:
                    print(f"\n{YELLOW}[error]{RESET} {e}")
            else:
                print(f"{MAGENTA}assistant>{RESET} ", end="", flush=True)
                try:
                    text = get_model_response(**kwargs)  # single string
                    print(text)
                    messages.append({"role": "assistant", "content": text})
                except Exception as e:
                    print(f"{YELLOW}[error]{RESET} {e}")


# ===================== ENTRY POINT =====================

if __name__ == "__main__":
    parser = make_parser()
    args = parser.parse_args()

    client = get_client(
        model=args.model,
        api_key_var=args.api_key_var,
        api_base_url=args.base_url,
        endpoints_path=args.endpoints_path,
    )

    # Always construct an MCPManager (defaults to ./mcp.json next to client.py)
    mcp_path = parse_mcp_path(args.mcp_config)
    mcp_manager = MCPManager(mcp_path)

    # Streaming is ON by default unless tools are enabled at startup.
    stream_default = bool(not args.tools)

    print(f"{DIM}Model:{RESET} {args.model} | {DIM}Base URL:{RESET} {args.base_url} | {DIM}Key Var:{RESET} {args.api_key_var}")
    print(f"{DIM}MCP config:{RESET} {mcp_path}")

    run_repl(
        client=client,
        model=args.model,
        system_prompt=args.system,
        stream_default=stream_default,
        mcp_manager=mcp_manager,
        tools_default=bool(args.tools),
        max_tool_rounds=args.max_tool_rounds,
        timeout=args.timeout,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )
