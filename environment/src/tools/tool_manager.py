# environment/src/tools/tool_manager.py
from __future__ import annotations

import importlib
import inspect
import pkgutil
from dataclasses import dataclass, field
from types import ModuleType
from typing import Any, Callable, Dict, Optional

from verifiers.utils.tool_utils import convert_func_to_oai_tool


@dataclass
class ToolSpec:
    """
    A registered tool callable with metadata.
    `callable` should be a plain async or sync function.
    """
    name: str
    callable: Callable[..., Any]
    module: str
    enabled: bool = True
    meta: Dict[str, Any] = field(default_factory=dict)


class ToolManager:
    """
    Discovers and manages tool callables.

    Convention for tool definition modules (in `definitions_pkg`):
      - Export a top-level function `register_tools(session_manager) -> dict[str, Callable]`
        returning a mapping of tool_name -> callable.
        Each callable may accept `(session_id=..., **kwargs)` to access per-session clients
        via `session_manager.require_session(session_id)`.
      - (Alternatively) Export a top-level dict `TOOLS: dict[str, Callable]`.

    You can filter which tools load via `tool_name_filter`.
    You can enable/disable tools at runtime.
    """

    def __init__(
        self,
        *,
        session_manager,
        definitions_pkg: str = "src.tools.definitions",
        tool_name_filter: Optional[Callable[[str], bool]] = None,
    ):
        self._sm = session_manager
        self._pkg_name = definitions_pkg
        self._filter = tool_name_filter
        self._tools: Dict[str, ToolSpec] = {}

        self._discover_and_register()
        

    # -------- Public API --------

    def list(self, include_disabled: bool = False) -> Dict[str, ToolSpec]:
        if include_disabled:
            return dict(self._tools)
        return {k: v for k, v in self._tools.items() if v.enabled}

    def enable(self, name: str) -> None:
        self._require(name).enabled = True

    def disable(self, name: str) -> None:
        self._require(name).enabled = False

    def get_callable(self, name: str) -> Callable[..., Any]:
        spec = self._require(name)
        if not spec.enabled:
            raise RuntimeError(f"Tool '{name}' is disabled.")
        return spec.callable

    def as_oai_tools(self) -> list[dict]:
        """
        Return OpenAI/Verifiers-compatible tool schema list.
        Falls back to an empty list if the helper is unavailable.
        """
        out = []
        for spec in self.list().values():
            try:
                out.append(convert_func_to_oai_tool(spec.callable))
            except Exception as e:
                print(f"[tool schema error] {spec.name}: {e!r}")
        return out

    # -------- Internals --------

    def _require(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}. Known: {', '.join(self._tools.keys()) or '(none)'}")
        return self._tools[name]

    def _discover_and_register(self) -> None:
        """
        Import all modules under the definitions package and register tools found there.
        """
        pkg = importlib.import_module(self._pkg_name)
        for modinfo in pkgutil.iter_modules(pkg.__path__, prefix=pkg.__name__ + "."):
            module = importlib.import_module(modinfo.name)
            self._register_from_module(module)

    def _register_from_module(self, module: ModuleType) -> None:
        """
        Accept either:
          - register_tools(session_manager) -> dict[name, callable]
          - TOOLS: dict[name, callable]
        """
        tools_dict: Dict[str, Callable[..., Any]] = {}

        if hasattr(module, "register_tools") and callable(getattr(module, "register_tools")):
            produced = module.register_tools(self._sm)  # type: ignore
            if not isinstance(produced, dict):
                raise TypeError(f"{module.__name__}.register_tools(...) must return dict[str, Callable]")
            tools_dict.update(produced)

        if hasattr(module, "TOOLS") and isinstance(getattr(module, "TOOLS"), dict):
            tools_dict.update(dict(getattr(module, "TOOLS")))

        if not tools_dict:
            return

        for name, fn in tools_dict.items():
            if not callable(fn):
                raise TypeError(f"Tool '{name}' in {module.__name__} is not callable.")
            if self._filter and not self._filter(name):
                continue

            wrapped = self._bind_session_manager_if_requested(fn)

            self._tools[name] = ToolSpec(
                name=name,
                callable=wrapped,
                module=module.__name__,
                enabled=True,
                meta={"source": module.__name__},
            )

    def _bind_session_manager_if_requested(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """
        If the function accepts a keyword-only param named 'session_manager' or 'sm',
        auto-inject it at call-time. This keeps tool definitions clean and testable.

        Tools still receive user params like (session_id=..., **kwargs) unchanged.
        """
        try:
            sig = inspect.signature(fn)
        except (TypeError, ValueError):
            return fn

        inject_params = {p.name for p in sig.parameters.values()
                         if p.kind in (p.KEYWORD_ONLY, p.POSITIONAL_OR_KEYWORD)}

        wants_sm = ("session_manager" in inject_params) or ("sm" in inject_params)
        if not wants_sm:
            return fn

        def _call_with_sm(*args, **kwargs):
            kwargs.setdefault("session_manager", self._sm)
            return fn(*args, **kwargs)

        return _call_with_sm
