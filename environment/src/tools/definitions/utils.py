import re
import hashlib
import json 
from typing import Any, Dict, List, Optional, Tuple

def _json_minified(obj: Any) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False)

def _json_pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)

def _counts(s: str) -> Tuple[int, int]:
    b = len(s.encode("utf-8"))
    lines = s.count("\n") + 1 if s else 0
    return b, lines

def _top_level_keys(obj: Any) -> List[str]:
    if isinstance(obj, dict):
        return sorted(list(obj.keys()))
    return []

def _per_resource_type_keys(bundle: dict) -> Dict[str, List[str]]:
    per: Dict[str, set] = {}
    entries = bundle.get("entry") or []
    if not isinstance(entries, list):
        return {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        r = e.get("resource")
        if not isinstance(r, dict):
            continue
        rt = r.get("resourceType") or "Unknown"
        per.setdefault(rt, set()).update(r.keys())
    return {rt: sorted(list(keys)) for rt, keys in per.items()}

def _resource_counts(bundle: dict) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    entries = bundle.get("entry") or []
    if not isinstance(entries, list):
        return {}
    for e in entries:
        r = e.get("resource") if isinstance(e, dict) else None
        rt = r.get("resourceType") if isinstance(r, dict) else None
        if rt:
            counts[rt] = counts.get(rt, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

def _slug(s: str, *, max_len: int = 80) -> str:
    s = (s or "").strip()
    s = s.lower()
    s = re.sub(r"[^a-z0-9._-]+", "-", s)
    s = s.strip("-").strip("_")
    if not s:
        s = "unnamed"
    return s[:max_len]

def _short_hash(s: str, n: int = 10) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:n]

def _identifier_from_resource(res: dict) -> str:
    """
    Prefer .id, then first identifier.value (optionally system tail), else sha1(data).
    """
    rid = res.get("id")
    if isinstance(rid, str) and rid:
        return _slug(rid)

    ident = res.get("identifier")
    if isinstance(ident, list) and ident:
        first = ident[0] if isinstance(ident[0], dict) else None
        if isinstance(first, dict):
            val = first.get("value")
            sys = first.get("system")
            if isinstance(val, str) and val:
                if isinstance(sys, str) and sys:
                    tail = sys.rstrip("/").split("/")[-1]
                    return _slug(f"{tail}_{val}")
                return _slug(val)

    return f"h{_short_hash(_json_minified(res))}"

def _identifier_for_bundle(request: str) -> str:
    """
    Derive a readable id from the request string plus a short hash to avoid collisions.
    """
    base = _slug(request, max_len=50)
    return f"{base}_{_short_hash(request)}"

def _save_path(resource_type: str | None, identifier: str, explicit: Optional[str]) -> str:
    """
    Relative save path like: fhir/<resourceType>/<identifier>.json
    If explicit is provided, use it verbatim (could be relative or absolute).
    """
    if explicit:
        return explicit
    rt = resource_type or "unknown"
    return f"fhir/{_slug(rt)}/{_slug(identifier)}.json"

async def _write_file_to_container(*, session_manager, session_id: str, file_path: str, content_b64: str) -> Dict[str, Any]:
    """
    Create parent dir and write base64 content using bash+base64 (busybox ok).
    Paths are treated as-is; callers supply relative paths (e.g., fhir/Patient/123.json).
    """
    ctx = session_manager.require_session(session_id)
    parent = file_path.rsplit("/", 1)[0] if "/" in file_path else "."
    cmd = (
        f'mkdir -p "{parent}" && '
        f'printf %s \'{content_b64}\' | base64 -d > "{file_path}" && '
        f'stat -c "%s" "{file_path}" 2>/dev/null || wc -c < "{file_path}"'
    )
    return await ctx.terminal_client.execute_command(
        session_id=session_id,
        command="bash",
        args=["-lc", cmd],
        timeout_seconds=30,
    )