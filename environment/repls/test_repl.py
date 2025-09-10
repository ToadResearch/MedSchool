# environment/repls/test_repl.py
# Tiny async REPL to exercise SessionManager + tools (explicit tool calls only)
# Run from repo root:  python -m environment.main
from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
from dataclasses import dataclass
from typing import Any, Dict

# Make sure "environment" can be imported when running from repo root
if __package__ is None and __name__ == "__main__":
    sys.path.append(os.path.abspath("."))

from src import SessionManager, get_settings, dump_settings_json  # type: ignore


def colored(st, color: str | None, background=False):
    return (
        f"\u001b[{10*background+60*(color.upper() == color)+30+['black','red','green','yellow','blue','magenta','cyan','white'].index(color.lower())}m{st}\u001b[0m"
        if color is not None
        else st
    )  # replace termcolor with simple ANSI


def jdump(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False)


HELP_TEXT = r"""
Commands
========
help                                    Show this help
settings                                Print resolved settings (timeout, classes, endpoints)
tools                                   List enabled tools
tools all                               List all tools (incl. disabled)
enable <tool>                           Enable a tool by name for this REPL session
disable <tool>                          Disable a tool by name for this REPL session

sessions                                List active session IDs
start [mem=256] [cpus=0.5] [image=...]  Start a full session via SessionManager (requires FHIR base if your code does)
start-term [mem=256] [cpus=0.5] [image=...]  Start a *terminal-only* session (bypasses FHIR init)*
stop <session_id>                       Stop a session
stop-all                                Stop all sessions

call <tool> <session_id> <json>         Call any tool by name with kwargs JSON (session_id is injected automatically)

Notes
-----
• start-term: your current SessionManager always builds a FHIR client; if you haven't configured a FHIR base URL,
  'start' may raise. 'start-term' creates a session through the same Sessions API, then registers it in the manager
  with a TerminalClient only so you can still test terminal/openFDA/terminology tools.
• For JSON args, use proper JSON (not Python dicts). Strings need quotes.
  Example:
    call fhir_get <sid> {"path":"Patient?_count=1"}
"""


@dataclass
class REPL:
    sm: SessionManager

    async def ainit(self):
        return self

    # ---------- util parsing ----------
    def _kv(self, parts):
        out: Dict[str, str] = {}
        for p in parts:
            if "=" in p:
                k, v = p.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    # ---------- commands ----------
    async def cmd_help(self, *_):
        print(colored(HELP_TEXT, "yellow"))

    async def cmd_settings(self, *_):
        try:
            print(dump_settings_json())
        except Exception as e:
            s = get_settings()
            print(
                json.dumps(
                    {
                        "timeout_s": s.timeout_s,
                        "classes": list(s.classes.keys()),
                    },
                    indent=2,
                )
            )
            print(f"\n(warning: dump_settings_json() failed: {e})")

    async def cmd_tools(self, *args):
        include_disabled = (len(args) >= 1 and str(args[0]).lower() == "all")
        tools = {
            name: {"module": spec.module, "enabled": spec.enabled}
            for name, spec in self.sm.tools.list(include_disabled=True).items()
        }
        if not include_disabled:
            tools = {k: v for k, v in tools.items() if v["enabled"]}
        print(jdump(tools))

    async def cmd_enable(self, name: str | None = None, *_):
        if not name:
            print(colored("usage: enable <tool>", "red"))
            return
        try:
            self.sm.tools.enable(name)
            print(colored(f"enabled: {name}", "green"))
        except Exception as e:
            print(colored(f"enable error: {e}", "red"))

    async def cmd_disable(self, name: str | None = None, *_):
        if not name:
            print(colored("usage: disable <tool>", "red"))
            return
        try:
            self.sm.tools.disable(name)
            print(colored(f"disabled: {name}", "green"))
        except Exception as e:
            print(colored(f"disable error: {e}", "red"))

    async def cmd_sessions(self, *_):
        print(jdump({"active": self.sm.active_session_ids}))

    async def cmd_start(self, *args):
        kv = self._kv(args)
        try:
            mem = int(kv.get("mem", ""))
        except Exception:
            mem = None
        try:
            cpus = float(kv.get("cpus", ""))
        except Exception:
            cpus = None
        image = kv.get("image")
        try:
            ctx = await self.sm.start_session(mem_mb=mem, cpus=cpus, image=image)
            print(jdump({"session_id": ctx.session_id, "note": "started via SessionManager.start_session"}))
        except Exception as e:
            print(colored(f"start error: {e}\nTip: If you haven't configured FHIR, try 'start-term' instead.", "red"))

    async def cmd_start_term(self, *args):
        """
        Terminal-only session that bypasses FHIR client construction.
        Uses the same Sessions API under the hood, then registers a SessionContext.
        """
        from src.session_manager import SessionContext  # type: ignore
        from src.clients.terminal_client import TerminalClient  # type: ignore

        kv = self._kv(args)
        mem = int(kv["mem"]) if "mem" in kv else None
        cpus = float(kv["cpus"]) if "cpus" in kv else None
        image = kv.get("image")

        try:
            sid = await self.sm.sessions_api.create_session(mem_mb=mem, cpus=cpus, image=image)
            term = TerminalClient(timeout_s=self.sm._timeout_s, client=self.sm._http)  # type: ignore[attr-defined]
            ctx = SessionContext(session_id=sid, fhir_client=None, terminal_client=term, meta={"terminal_only": True})
            self.sm._sessions[sid] = ctx  # dev-only; acceptable for a REPL
            print(jdump({"session_id": sid, "note": "terminal-only session registered"}))
        except Exception as e:
            print(colored(f"start-term error: {e}", "red"))

    async def cmd_stop(self, sid: str | None = None, *_):
        if not sid:
            print(colored("usage: stop <session_id>", "red"))
            return
        ok = await self.sm.stop_session(sid)
        print(jdump({"session_id": sid, "stopped": ok}))

    async def cmd_stop_all(self, *_):
        await self.sm.stop_all()
        print(jdump({"stopped_all": True}))

    async def cmd_call(self, tool: str | None = None, sid: str | None = None, *rest):
        if not tool or not sid:
            print(
                colored(
                    'usage: call <tool> <session_id> <json-kwargs>\nexample: call fhir_get <sid> {"path":"Patient?_count=1"}',
                    "red",
                )
            )
            return
        kwargs_json = " ".join(rest).strip()
        if not kwargs_json:
            kwargs = {}
        else:
            try:
                kwargs = json.loads(kwargs_json)
            except Exception as e:
                print(colored(f"JSON parse error: {e}\nGot: {kwargs_json}", "red"))
                return
        try:
            fn = self.sm.tools.get_callable(tool)
        except Exception as e:
            print(colored(f"unknown/disabled tool '{tool}': {e}", "red"))
            return
        try:
            kwargs["session_id"] = sid
            result = await fn(**kwargs)
            print(jdump(result))
        except Exception as e:
            print(colored(f"tool error: {e}", "red"))

    # ---------- REPL loop ----------
    async def loop(self):
        print(colored("env REPL. type 'help' for commands. Ctrl+C to quit.", "blue"))
        print(colored(HELP_TEXT, "yellow"))
        while True:
            try:
                print(colored("> ", "cyan"), end="")
                line = input("").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue

            if line.lower() in ("exit", "quit"):
                break

            parts = shlex.split(line)
            cmd = parts[0]
            args = parts[1:]

            dispatch = {
                "help": self.cmd_help,
                "settings": self.cmd_settings,
                "tools": self.cmd_tools,
                "enable": self.cmd_enable,
                "disable": self.cmd_disable,
                "sessions": self.cmd_sessions,
                "start": self.cmd_start,
                "start-term": self.cmd_start_term,
                "stop": self.cmd_stop,
                "stop-all": self.cmd_stop_all,
                "call": self.cmd_call,
            }

            func = dispatch.get(cmd)
            if not func:
                print(colored(f"unknown command: {cmd} (type 'help')", "red"))
                continue

            try:
                await func(*args)
            except TypeError as te:
                print(colored(f"usage error: {te}", "red"))
            except Exception as e:
                print(colored(f"error: {e}", "red"))

    async def aclose(self):
        try:
            await self.sm.stop_all()
        finally:
            await self.sm.aclose()


async def _amain():
    sm = SessionManager()
    repl = await REPL(sm).ainit()
    try:
        await repl.loop()
    finally:
        await repl.aclose()


def main():
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
