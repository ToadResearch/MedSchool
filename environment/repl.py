# environment/repl.py
from __future__ import annotations

import asyncio
import difflib
import json
import os
import random
import sys
from typing import Any, Dict, List, Optional, Tuple

# --------- robust imports: works as "python -m environment.repl" or "python environment/repl.py"
try:
    from .src.config import AppConfig
    from .src.fhir_client import AsyncFHIRClient
except Exception:
    import pathlib as _p
    _HERE = _p.Path(__file__).resolve()
    _PKG = _HERE.parent
    _ROOT = _PKG.parent
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from environment.src.config import AppConfig
    from environment.src.fhir_client import AsyncFHIRClient
# -----------------------------------------------------------------------------------------------

# ===================== TTY COLORS =====================
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
MAGENTA = "\033[35m"
BLUE = "\033[34m"
GRAY = "\033[90m"
BRIGHT_CYAN = "\033[96m"
BRIGHT_GREEN = "\033[92m"
BRIGHT_MAGENTA = "\033[95m"

def _hr(w: int = 70) -> str: return "─" * w
def _print_rule(w: int = 70): print(f"{DIM}{_hr(w)}{RESET}")
def _info(msg: str): print(f"{DIM}{msg}{RESET}")
def _warn(msg: str): print(f"{YELLOW}{msg}{RESET}")

def _clear() -> None:
    try: os.system("cls" if os.name == "nt" else "clear")
    except Exception: pass

def _pretty(obj: Any) -> str:
    try:
        return json.dumps(obj, indent=2, ensure_ascii=False)
    except Exception:
        return str(obj)

def _prompt(msg: str) -> str:
    try: return input(msg)
    except EOFError: return ""

def _print_table(rows: List[Tuple[str, str]], headers: Tuple[str, str] = ("ResourceType", "Count")) -> None:
    c1 = max(len(headers[0]), *(len(r[0]) for r in rows)) if rows else len(headers[0])
    c2 = max(len(headers[1]), *(len(r[1]) for r in rows)) if rows else len(headers[1])
    print(f"{BOLD}{headers[0]:<{c1}}  {headers[1]:>{c2}}{RESET}")
    print(f"{'-'*c1}  {'-'*c2}")
    for a, b in sorted(rows, key=lambda x: x[0].lower()):
        print(f"{a:<{c1}}  {b:>{c2}}")


# ===================== BANNER / HELP =====================
def _help():
    print(f"{MAGENTA}{BOLD}Controls{RESET}")
    _print_rule()
    print(f"  {CYAN}1{RESET}   Random resource across ALL types")
    print(f"  {CYAN}2{RESET}   List counts for each resource type")
    print(f"  {CYAN}3{RESET}   Random resource BY TYPE")
    print(f"  {CYAN}4{RESET}   Read a specific resource (type + {BOLD}id or identifier{RESET})")
    print(f"  {CYAN}5{RESET}   Show CapabilityStatement basics")
    print(f"  {CYAN}t{RESET}   List available resource types")
    print(f"  {CYAN}r{RESET}   Refresh resource types from /metadata")
    print(f"  {CYAN}m{RESET}   Show this menu again")
    print(f"  {CYAN}c{RESET}   Clear screen")
    print(f"  {CYAN}q{RESET}   Quit\n")

def _banner(base_url: str):
    print(f"{BOLD}{BRIGHT_MAGENTA}FHIR REPL ready{RESET} — using {CYAN}FHIR_BASE_URL{RESET}: {base_url}\n")
    _help()


# ===================== FHIR HELPERS =====================
async def _resource_types(client: AsyncFHIRClient) -> List[str]:
    caps = await client.get_capability()
    types: List[str] = []
    for rest in caps.get("rest", []) or []:
        for r in rest.get("resource", []) or []:
            t = r.get("type")
            if isinstance(t, str):
                types.append(t)
    return sorted(set(types))

async def _counts_by_type(client: AsyncFHIRClient, types: List[str]) -> List[Tuple[str, int]]:
    sem = asyncio.Semaphore(12)
    async def _count_one(t: str) -> Tuple[str, int]:
        async with sem:
            try:
                return t, int(await client.count(t))
            except Exception:
                return t, -1
    return await asyncio.gather(*(_count_one(t) for t in types))

async def _random_across_types(client: AsyncFHIRClient, types: List[str]) -> Optional[Dict[str, Any]]:
    counts = await _counts_by_type(client, types)
    nonzero = [(t, c) for t, c in counts if c and c > 0]
    if not nonzero: return None
    maxw = max(c for _, c in nonzero)
    scale = max(1, maxw // 1_000_000)
    weighted = [(t, max(1, c // scale)) for t, c in nonzero]
    pick = random.choices([t for t, _ in weighted], weights=[w for _, w in weighted], k=1)[0]
    return await client.sample(pick)

def _summarize(res: Dict[str, Any]) -> str:
    rt = res.get("resourceType")
    if rt == "Patient":
        ids = res.get("identifier", [])
        id_strs = []
        for i in ids[:3]:
            sys_ = i.get("system", ""); val = i.get("value", "")
            id_strs.append(f"{sys_}|{val}" if sys_ and val else (val or ""))
        names = []
        for n in res.get("name", [])[:2]:
            family = n.get("family", ""); given = " ".join(n.get("given", [])[:3])
            parts = " ".join(p for p in [given, family] if p); 
            if parts: names.append(parts)
        demo = []
        if res.get("gender"): demo.append(str(res["gender"]))
        if res.get("birthDate"): demo.append(str(res["birthDate"]))
        return f"Patient(id={res.get('id','?')}, name={'; '.join(names) or '—'}, ids={'; '.join([s for s in id_strs if s] ) or '—'}, {', '.join(demo) or '—'})"
    if rt == "Observation":
        code = res.get("code", {}) or {}
        text = code.get("text") or ""
        codings = code.get("coding", []) or []
        first_code = ""
        if codings:
            c0 = codings[0]
            first_code = f"{c0.get('system','')}|{c0.get('code','')}".strip("|")
        val = res.get("valueQuantity") or res.get("valueCodeableConcept") or res.get("valueString")
        val_str = ""
        if isinstance(val, dict) and "value" in val:
            v = val.get("value"); u = val.get("unit") or val.get("code") or ""
            val_str = f"{v} {u}".strip()
        elif isinstance(val, dict) and "text" in val:
            val_str = val.get("text", "")
        elif isinstance(val, str):
            val_str = val
        label = text or first_code or "—"
        return f"Observation(id={res.get('id','?')}, code={label}, value={val_str or '—'})"
    return f"{rt}(id={res.get('id','—')})"


# -------- identifier-aware read helper --------
async def _read_by_id_or_identifier(client: AsyncFHIRClient, rtype: str, token: str) -> tuple[dict | None, str]:
    """
    Token may be:
      • server id (e.g., 1189)
      • identifier 'value'
      • identifier 'system|value'
    Returns (resource or None, mode_used)
    """
    token = (token or "").strip()
    if not token:
        return None, "empty"
    # system|value → identifier search w/ system
    if "|" in token:
        system, value = token.split("|", 1)
        res = await client.read_by_identifier(rtype, value=value.strip(), system=system.strip())
        return res, "identifier(system|value)"
    # purely digits? likely server ID
    if token.isdigit():
        try:
            res = await client.read(rtype, token)
            return res, "server-id"
        except Exception:
            # fall back to identifier value match if read fails
            pass
    # default: identifier value only
    res = await client.read_by_identifier(rtype, value=token)
    return res, "identifier(value)"


# -------- friendly 'ask for a valid type' helper --------
def _ask_valid_type(types: List[str], *, prompt_label: str = "type") -> Optional[str]:
    """
    Prompt until the user enters a valid FHIR resource type from `types`.
    Returns the chosen type (exactly as in `types`) or None if user cancels.
    Special inputs:
      - '?' prints all available types.
      - empty input cancels (returns None).
    """
    types_set = {t.lower(): t for t in types}
    while True:
        raw = _prompt(
            f"{BLUE}{prompt_label} (FHIR type, e.g., Patient, Observation; '?' to list){RESET} "
        ).strip()
        if not raw:
            return None
        if raw == "?":
            print(f"{BOLD}Available resource types ({len(types)}):{RESET}")
            print(", ".join(types))
            continue
        # case-insensitive exact match
        exact = types_set.get(raw.lower())
        if exact:
            return exact
        # show close matches
        guesses = difflib.get_close_matches(raw, types, n=5, cutoff=0.6)
        _warn(f"Unknown type: {raw}")
        if guesses:
            print(f"{DIM}Did you mean:{RESET} " + ", ".join(guesses))
        # loop and ask again


# ===================== MAIN REPL =====================
async def repl() -> None:
    cfg = AppConfig.load(timeout_s=30.0)  # loads FHIR_BASE_URL from .env via python-dotenv
    base = cfg.fhir_base_url
    _clear()
    _banner(base)

    async with AsyncFHIRClient(base_url=base, timeout_s=cfg.timeout_s) as client:
        try:
            types = await _resource_types(client)
        except Exception as e:
            _warn(f"Failed to fetch CapabilityStatement from {base}/metadata: {e}")
            sys.exit(1)

        _info(f"{len(types)} resource types available. Press {CYAN}m{RESET} for menu.")

        while True:
            try:
                choice = _prompt(f"{GREEN}repl>{RESET} ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not choice:
                continue

            # quick commands
            if choice in {"q", "quit", "exit"}:
                print("bye 👋"); return
            if choice in {"m", "menu", "help", "h"}:
                _help(); continue
            if choice in {"c", "clear"}:
                _clear()
                _help()
                continue
            if choice in {"t", "types"}:
                print(f"{BOLD}Resource types ({len(types)}):{RESET}")
                print(", ".join(types) if types else "—")
                continue
            if choice in {"r", "refresh"}:
                try:
                    types = await _resource_types(client)
                    _info(f"Refreshed. {len(types)} resource types available.")
                except Exception as e:
                    _warn(f"Error refreshing types: {e}")
                continue

            # numbered actions
            if choice == "1":
                print(f"{BRIGHT_CYAN}{BOLD}Random across ALL types…{RESET}")
                try:
                    res = await _random_across_types(client, types)
                    if not res: print("No resources available.")
                    else:
                        print(_summarize(res)); _print_rule(); print(_pretty(res))
                except Exception as e:
                    _warn(f"Error: {e}")
                continue

            if choice == "2":
                print(f"{BRIGHT_CYAN}{BOLD}Counting resources by type…{RESET}")
                try:
                    counts = await _counts_by_type(client, types)
                    rows = [(t, "Error" if c < 0 else f"{c:,}") for t, c in counts]
                    _print_table(rows)
                except Exception as e:
                    _warn(f"Error: {e}")
                continue

            if choice == "3":
                print(f"{BRIGHT_CYAN}{BOLD}Random resource by TYPE{RESET}")
                t = _ask_valid_type(types)
                if not t:
                    continue
                try:
                    res = await client.sample(t)
                    if not res: print(f"No {t} resources found.")
                    else:
                        print(_summarize(res)); _print_rule(); print(_pretty(res))
                except Exception as e:
                    _warn(f"Error: {e}")
                continue

            if choice == "4":
                print(f"{BRIGHT_CYAN}{BOLD}Read specific resource{RESET}")
                print(f"{DIM}Tip: enter a {BOLD}FHIR resource type{RESET}{DIM} like Patient or Observation at the next prompt.")
                print(f"     Then enter either a server id (e.g., 1189), an identifier value")
                print(f"     (e.g., 0e54…afc7), or system|value (e.g., http://hospital.smarthealthit.org|0e54…afc7).{RESET}")
                t = _ask_valid_type(types)
                if not t:
                    continue
                token = _prompt(f"{BLUE}id-or-identifier>{RESET} ").strip()
                if not token:
                    _warn("id/identifier is required.")
                    continue
                try:
                    res, mode = await _read_by_id_or_identifier(client, t, token)
                    if not res:
                        _warn(f"No match for {t} using {mode}.")
                        continue
                    print(f"{DIM}lookup mode:{RESET} {mode}")
                    print(_summarize(res)); _print_rule(); print(_pretty(res))
                except Exception as e:
                    _warn(f"Error: {e}")
                continue

            if choice == "5":
                print(f"{BRIGHT_CYAN}{BOLD}CapabilityStatement basics{RESET}")
                try:
                    caps = await client.get_capability()
                    fhir_ver = caps.get("fhirVersion", "unknown")
                    sw_name = caps.get("software", {}).get("name", "unknown")
                    sw_ver = caps.get("software", {}).get("version", "unknown")
                    fmts = caps.get("format", []) or []
                    print(f"{BOLD}FHIR Version:{RESET} {fhir_ver}")
                    print(f"{BOLD}Software:{RESET}     {sw_name} {sw_ver}")
                    print(f"{BOLD}Formats:{RESET}      {', '.join(fmts) if fmts else '—'}")
                    print(f"{BOLD}Types:{RESET}        {', '.join(types) if types else '—'}")
                except Exception as e:
                    _warn(f"Error: {e}")
                continue

            _warn("Unknown command. Press 'm' for menu.")


# ===================== ENTRY =====================
if __name__ == "__main__":
    try:
        asyncio.run(repl())
    except KeyboardInterrupt:
        print("\nbye 👋")
