# environment/src/config.py
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

import json
import os
import yaml
from dotenv import dotenv_values, find_dotenv, load_dotenv

load_dotenv()


# ===========================
# Data models
# ===========================

@dataclass(frozen=True)
class ToolLimit:
    timeout_s: Optional[float] = None
    max_results: Optional[int] = None

    @staticmethod
    def merge(base: "ToolLimit" | None, override: "ToolLimit" | None) -> "ToolLimit":
        """For overriding the default limits for the class of tools"""
        base = base or ToolLimit()
        override = override or ToolLimit()
        return ToolLimit(
            timeout_s=override.timeout_s if override.timeout_s is not None else base.timeout_s,
            max_results=override.max_results if override.max_results is not None else base.max_results,
        )


@dataclass(frozen=True)
class ToolConfig:
    """Represents a single tool under a class"""
    name: str
    enabled: bool
    limits: ToolLimit = field(default_factory=ToolLimit)


@dataclass(frozen=True)
class ToolClassConfig:
    """
    Represents a tool 'class' (e.g., FHIR, TERMINAL, OPENFDA):
      - base_url is resolved from env via <CLASS>_BASE_URL or <CLASS>_PROXY_PUBLIC_BASE
      - defaults apply to tools within the class unless overridden
      - tools: concrete tools under this class
    """
    name: str
    base_url: Optional[str]
    default_enabled: bool = False
    default_limits: ToolLimit = field(default_factory=ToolLimit)
    tools: Dict[str, ToolConfig] = field(default_factory=dict)


@dataclass(frozen=True)
class SandboxConfig:
    """
    Settings for the Alpine sandbox session orchestration.
    Values come from environments/configs/sandbox.yaml.
    """
    max_concurrent_sessions: int
    exec_timeout_s: int
    default_mem_mb: int
    default_cpus: float


@dataclass(frozen=True)
class Settings:
    """
    One struct to read everything:
      • timeout_s        : global default timeout
      • classes          : mapping of class name → ToolClassConfig
      • tool_to_class    : tool name → class name
      • enabled          : flattened list of enabled tool names (back-compat)
      • disabled : flattened list (defined but off)
      • limits           : flattened tool limits dict (back-compat)

    Helpers:
      s.is_enabled("fhir_get")
      s.limit("fhir_get")
      s.base_url_for_tool("fhir_get")   # -> the class base url
      s.class_for_tool("fhir_get")      # -> "FHIR"
    """
    timeout_s: float
    classes: Dict[str, ToolClassConfig]
    tool_to_class: Dict[str, str]

    # Back-compat flat views
    enabled: List[str]
    disabled: List[str]
    limits: Dict[str, ToolLimit]

    # ---- sandbox & routing ----
    sandbox: SandboxConfig
    # Canonical base for session lifecycle endpoints (middleman /sessions)
    session_base_url: Optional[str]

    # ---- helpers ----
    def is_enabled(self, tool_name: str) -> bool:
        cfg = self._tool_cfg(tool_name)
        return cfg.enabled if cfg else False

    def limit(self, tool_name: str) -> Optional[ToolLimit]:
        return self.limits.get(tool_name)

    def class_for_tool(self, tool_name: str) -> Optional[str]:
        return self.tool_to_class.get(tool_name)

    def base_url_for_tool(self, tool_name: str) -> Optional[str]:
        cls_name = self.tool_to_class.get(tool_name)
        if not cls_name:
            return None
        cls_cfg = self.classes.get(cls_name)
        return cls_cfg.base_url if cls_cfg else None

    def base_url_for_class(self, class_name: str) -> Optional[str]:
        cfg = self.classes.get(class_name)
        return cfg.base_url if cfg else None

    def _tool_cfg(self, tool_name: str) -> Optional[ToolConfig]:
        cls_name = self.tool_to_class.get(tool_name)
        if not cls_name:
            return None
        cls_cfg = self.classes.get(cls_name)
        if not cls_cfg:
            return None
        return cls_cfg.tools.get(tool_name)

    # Convenience: legacy direct properties if you want them
    @property
    def fhir_base_url(self) -> Optional[str]:
        return self.base_url_for_class("FHIR")

    @property
    def terminal_base_url(self) -> Optional[str]:
        return self.base_url_for_class("TERMINAL")





# ===========================
# Loaders
# ===========================

# Append path suffix when resolving from *_PROXY_PUBLIC_BASE
_PROXY_SUFFIX_BY_CLASS: Dict[str, str] = {
    "FHIR": "/fhir",
    "TERMINAL": "/terminal",
}


def _read_env_chain() -> Dict[str, str]:
    """Merge .env (if present) and real process env (process env wins)."""
    values: Dict[str, str] = {}
    env_path = find_dotenv(usecwd=True)
    if env_path:
        values |= {k: str(v) for k, v in dotenv_values(env_path, verbose=False, interpolate=True).items() if v is not None}
    values |= {k: str(v) for k, v in os.environ.items() if v is not None}
    return values


def _resolve_base_for_class(vals: Dict[str, str], class_name: str) -> Optional[str]:
    """
    Resolve base URL for a tool class using generic env scheme:
      <CLASS>_BASE_URL
      <CLASS>_PROXY_PUBLIC_BASE (+ class-specific suffix if defined)
    CLASS must be upper snake (e.g., 'FHIR', 'TERMINAL', 'OPENFDA').
    """
    key_base = f"{class_name}_BASE_URL"
    key_proxy = f"{class_name}_PROXY_PUBLIC_BASE"
    base = vals.get(key_base)
    if base:
        return base.rstrip("/")
    proxy = vals.get(key_proxy)
    if proxy:
        suffix = _PROXY_SUFFIX_BY_CLASS.get(class_name, "")
        return f"{proxy.rstrip('/')}{suffix}".rstrip("/")
    # Not all classes require a base URL (e.g., optional tool packs)
    return None


def _parse_tool_limit_node(node: Any) -> ToolLimit:
    node = node or {}
    return ToolLimit(
        timeout_s=node.get("timeout_s"),
        max_results=node.get("max_results"),
    )


def _search_upwards(start: Path, filename: str) -> Optional[Path]:
    """Walk up from `start` to filesystem root looking for `filename`."""
    cur = start.resolve()
    while True:
        candidate = cur / filename
        if candidate.exists():
            return candidate
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


# replace your current _find_configs_yaml with this

def _find_configs_yaml(filename: str, explicit: Optional[Path] = None) -> Optional[Path]:
    """
    Resolve YAML files under environments/configs/ (or environment/configs/), with sensible fallbacks.
      Priority:
        1) explicit absolute or CWD-relative path (if provided)
        2) walk up from: this file's dir, CWD, and the folder containing .env
           trying: environments/configs/<filename>, environment/configs/<filename>, configs/<filename>
        3) legacy fallbacks (../<filename>, CWD/<filename>, and upward searches)
    """
    # 1) explicit path provided by caller
    if explicit:
        p = explicit if explicit.is_absolute() else (Path.cwd() / explicit)
        return p.resolve() if p.exists() else None

    # Build starting points
    starts: list[Path] = [Path(__file__).resolve().parent, Path.cwd()]
    try:
        env_path = find_dotenv(usecwd=True)
        if env_path:
            starts.append(Path(env_path).resolve().parent)
    except Exception:
        pass

    # Deduplicate while preserving order
    seen = set()
    uniq_starts = []
    for s in starts:
        if s not in seen:
            uniq_starts.append(s)
            seen.add(s)

    # 2) Preferred: look for known config layouts while walking up
    def _seek_from(start: Path) -> Optional[Path]:
        cur = start.resolve()
        while True:
            for prefix in ("environments/configs", "environment/configs", "configs"):
                candidate = cur / prefix / filename
                if candidate.exists():
                    return candidate
            # also try legacy flat at this level
            legacy = cur / filename
            if legacy.exists():
                return legacy
            if cur.parent == cur:
                break
            cur = cur.parent
        return None

    for base in uniq_starts:
        found = _seek_from(base)
        if found:
            return found

    # 3) Legacy: walk up looking for the bare filename from two anchors
    here_parent = Path(__file__).resolve().parent.parent
    return _search_upwards(here_parent, filename) or _search_upwards(Path.cwd(), filename)


def _load_tools_yaml(yaml_path: Optional[Path]) -> Dict[str, Any]:
    """
    Load hierarchical tools.yaml. By default resolves environments/configs/tools.yaml,
    with legacy fallbacks described in _find_configs_yaml().
    """
    path = _find_configs_yaml("tools.yaml", yaml_path)
    if not path:
        return {}
    raw = yaml.safe_load(path.read_text()) or {}
    return raw

def _load_sandbox_yaml(yaml_path: Optional[Path]) -> Dict[str, Any]:
    path = _find_configs_yaml("sandbox.yaml", yaml_path)
    if not path:
        return {}
    return yaml.safe_load(path.read_text()) or {}

def _build_classes_from_yaml(vals_env: Dict[str, str], raw_yaml: Dict[str, Any]) -> Tuple[Dict[str, ToolClassConfig], Dict[str, str]]:
    classes_node = (raw_yaml or {}).get("classes") or {}
    classes: Dict[str, ToolClassConfig] = {}
    tool_to_class: Dict[str, str] = {}

    for class_name, class_cfg in classes_node.items():
        cname = str(class_name).upper()  # normalize class key
        defaults_node = (class_cfg or {}).get("defaults") or {}
        default_enabled = bool(defaults_node.get("enabled", False))
        default_limits = _parse_tool_limit_node(defaults_node.get("limits"))

        # Resolve class base URL from env
        base_url = _resolve_base_for_class(vals_env, cname)

        # Tools under class
        tools_list = (class_cfg or {}).get("tools") or []
        tool_map: Dict[str, ToolConfig] = {}

        for t in tools_list:
            if isinstance(t, str):
                tname = t
                tenabled = default_enabled
                tlimits = default_limits
            else:
                tname = t.get("name")
                if not tname:
                    continue
                tenabled = t.get("enabled", default_enabled)
                tlimits = ToolLimit.merge(default_limits, _parse_tool_limit_node(t.get("limits")))
            tool_map[tname] = ToolConfig(name=tname, enabled=bool(tenabled), limits=tlimits)
            tool_to_class[tname] = cname

        classes[cname] = ToolClassConfig(
            name=cname,
            base_url=base_url,
            default_enabled=default_enabled,
            default_limits=default_limits,
            tools=tool_map,
        )

    return classes, tool_to_class


def _flatten_views(classes: Dict[str, ToolClassConfig]) -> Tuple[List[str], List[str], Dict[str, ToolLimit]]:
    enabled: List[str] = []
    disabled: List[str] = []
    limits: Dict[str, ToolLimit] = {}

    for cls in classes.values():
        for tname, tcfg in cls.tools.items():
            if tcfg.enabled:
                enabled.append(tname)
            else:
                disabled.append(tname)
            limits[tname] = tcfg.limits
    return enabled, disabled, limits


def _build_settings(
    *,
    tools_yaml_path: Optional[Path] = None,
    sandbox_yaml_path: Optional[Path] = None,
) -> Settings:
    vals_env = _read_env_chain()
    raw_yaml = _load_tools_yaml(tools_yaml_path)
    raw_sandbox = _load_sandbox_yaml(sandbox_yaml_path)

    # Global default timeout (used by some callers as a general knob)
    timeout_s = float(raw_yaml.get("timeout_s", vals_env.get("TIMEOUT_S", 30.0)))

    classes, tool_to_class = _build_classes_from_yaml(vals_env, raw_yaml)
    enabled, disabled, limits = _flatten_views(classes)

    # Back-compat: If no class structure provided, mirror old flat schema from legacy keys
    if not classes and ("enabled" in raw_yaml or "limits" in raw_yaml):
        # Legacy flat
        default_enabled = False
        legacy_enabled = list(raw_yaml.get("enabled") or [])
        legacy_disabled = list(raw_yaml.get("disabled") or [])
        legacy_limits_raw = raw_yaml.get("limits") or {}
        # Put everything under a synthetic class 'LEGACY' with no base_url
        tool_map = {}
        tool_to_class = {}
        for t in set(legacy_enabled + legacy_disabled + list(legacy_limits_raw.keys())):
            tenabled = t in legacy_enabled
            tlimit = _parse_tool_limit_node(legacy_limits_raw.get(t))
            tool_map[t] = ToolConfig(name=t, enabled=tenabled, limits=tlimit)
            tool_to_class[t] = "LEGACY"
        classes = {
            "LEGACY": ToolClassConfig(
                name="LEGACY",
                base_url=None,
                default_enabled=default_enabled,
                default_limits=ToolLimit(),
                tools=tool_map,
            )
        }
        enabled, disabled, limits = _flatten_views(classes)

    # ---- sandbox config ----
    sb_node = (raw_sandbox.get("sandbox") if isinstance(raw_sandbox, dict) else {}) or {}
    sandbox_cfg = SandboxConfig(
        
        max_concurrent_sessions=int(sb_node.get("max_concurrent_sessions", 0)),
        exec_timeout_s=int(sb_node.get("exec_timeout_s", 0)),
        default_mem_mb=int(sb_node.get("default_mem_mb", 0)),
        default_cpus=float(sb_node.get("default_cpus", 0)),
    )

    # ---- session base url (derived from env) ----
    # /sessions endpoint has no upstream proxy, it directly goes to the sessions api. so use middleman public base # TODO: consider changing this
    session_base = vals_env.get("MIDDLEMAN_PUBLIC_BASE", "").rstrip("/")
    session_base = f"{session_base}/sessions" if session_base else None

    return Settings(
        timeout_s=timeout_s,
        classes=classes,
        tool_to_class=tool_to_class,
        enabled=enabled,
        disabled=disabled,
        limits=limits,
        sandbox=sandbox_cfg,
        session_base_url=session_base,
    )


# ===========================
# Public API
# ===========================

def load(
    *,
    tools_yaml_path: Optional[str | Path] = None,
    sandbox_yaml_path: Optional[str | Path] = None,
) -> Settings:
    """Non-cached loader (tests / dynamic reloads)."""
    p_tools: Optional[Path] = Path(tools_yaml_path) if tools_yaml_path else None
    p_sbx: Optional[Path] = Path(sandbox_yaml_path) if sandbox_yaml_path else None
    return _build_settings(tools_yaml_path=p_tools, sandbox_yaml_path=p_sbx)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor for app/runtime use."""
    return _build_settings()


# JSON view for debugging
def dump_settings_json() -> str:
    s = get_settings()
    def lim2dict(l: ToolLimit | None):
        return None if l is None else {"timeout_s": l.timeout_s, "max_results": l.max_results}
    return json.dumps({
        "timeout_s": s.timeout_s,
        "classes": {
            k: {
                "base_url": v.base_url,
                "default_enabled": v.default_enabled,
                "default_limits": lim2dict(v.default_limits),
                "tools": {tn: {"enabled": tc.enabled, "limits": lim2dict(tc.limits)} for tn, tc in v.tools.items()},
            } for k, v in s.classes.items()
        },
        "enabled": s.enabled,
        "disabled": s.disabled,
        "limits": {k: lim2dict(v) for k, v in s.limits.items()},
        "sandbox": {
            "max_concurrent_sessions": s.sandbox.max_concurrent_sessions,
            "exec_timeout_s": s.sandbox.exec_timeout_s,
            "default_mem_mb": s.sandbox.default_mem_mb,
            "default_cpus": s.sandbox.default_cpus,
        },
        "session_base_url": s.session_base_url,
    }, indent=2, sort_keys=True)


if __name__ == "__main__":
    # run to see current resolved settings
    print(dump_settings_json())