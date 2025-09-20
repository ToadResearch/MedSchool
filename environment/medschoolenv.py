# environment/medschoolenv.py
# based on
#   - MultiTurnEnv: https://github.com/willccbb/verifiers/blob/d66990cc07f126ea35abc622ea60d788b7b9b9c7/verifiers/envs/multiturn_env.py
#   - ToolEnv:      https://github.com/willccbb/verifiers/blob/d66990cc07f126ea35abc622ea60d788b7b9b9c7/verifiers/envs/tool_env.py
from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from datasets import Dataset
from verifiers.envs.multiturn_env import MultiTurnEnv
from verifiers.types import (
    ChatCompletionMessageToolCall,
    Message,
    Messages,
    State,
    Info,
    SamplingArgs,
)
from verifiers import Parser, Rubric
from verifiers.utils.async_utils import maybe_await
from verifiers.utils.tool_utils import convert_func_to_oai_tool

from src import SessionManager, get_settings
import uuid

def _sanitize_oai_tools(tools_list: list[dict]) -> list[dict]:
    """
    Make tool schemas provider-friendly and session-safe:
      - Drop unsupported/annoying "strict" flags. TODO: probaly should remove this at some point. would need to get rid of Optional type hints.
      - Remove "session_id" from exposed tool parameter schemas so the LLM never supplies it.
      - Recompute 'required' so optional args remain optional across strict providers.
      - Deduplicate by function name.
    """

    def _allows_null(schema: dict | None) -> bool:
        """Return True if the JSON schema allows null."""
        if not isinstance(schema, dict):
            return False
        t = schema.get("type")
        if t == "null":
            return True
        if isinstance(t, list) and "null" in t:
            return True
        for key in ("anyOf", "oneOf"):
            arr = schema.get(key)
            if isinstance(arr, list):
                for s in arr:
                    if isinstance(s, dict) and s.get("type") == "null":
                        return True
        # Some generators use non-standard flags:
        if schema.get("nullable") is True or schema.get("x-nullable") is True:
            return True
        return False

    # TODO: update to check for None defaults or Optional type hints instead of manually writing these out
    # Minimal, canonical required sets for our tools.
    # Anything not listed uses heuristic trimming of the generator's 'required'.
    REQUIRED_BY_TOOL = {
        # Terminal
        "terminal_command": ["command"],

        # FHIR
        "fhir_get": ["request"],
        "fhir_post": ["path", "body_json"],
        "fhir_update": ["path", "body_json"],
        "fhir_delete": ["path"],
        "fhir_submit_bundle": ["bundle"],
        "fhir_validate": ["resource_json"],
        "fhir_doc": ["resource_type"],
        "fhir_fetch_full": ["file_path"],
        "fhir_pipe_json": ["file_path", "command"],

        # Terminology
        "code_lookup": ["code"],

        # openFDA
        "openfda_adverse_events": ["drug_name"],
        "openfda_label": ["drug_name"],
        "openfda_recalls": ["drug_name"],
        "openfda_drug_shortages": ["drug_name"],
    }

    out: list[dict] = []
    seen: set[str] = set()

    for t in tools_list or []:
        t = dict(t)  # shallow copy

        # ---- 1) Remove any top-level "strict"
        t.pop("strict", None)

        fn = t.get("function")
        if isinstance(fn, dict):
            fn = dict(fn)
            fn.pop("strict", None)  # nested strict

            # ---- 2) Tidy the parameters schema
            params = fn.get("parameters")
            if isinstance(params, dict):
                params = dict(params)

                # Properties
                props = params.get("properties")
                if not isinstance(props, dict):
                    props = {}

                # Hide session_id from schema entirely
                if "session_id" in props:
                    props = dict(props)
                    props.pop("session_id", None)

                # Current 'required' from generator
                req = params.get("required")
                if not isinstance(req, list):
                    req = []

                # Remove session_id from required (if present)
                req = [r for r in req if r != "session_id"]

                # ---- 3) Fix over-strict 'required'
                fname = (fn.get("name") or "").strip()

                if fname in REQUIRED_BY_TOOL:
                    # Use our curated list, but only keep fields that actually exist in props
                    new_required = [r for r in REQUIRED_BY_TOOL[fname] if r in props]
                else:
                    # Heuristic: keep only those that do NOT allow null and have no default
                    new_required = []
                    for r in req:
                        sch = props.get(r, {})
                        if _allows_null(sch):
                            continue
                        if isinstance(sch, dict) and ("default" in sch):
                            continue
                        new_required.append(r)

                # Reassign sanitized properties/required
                params["properties"] = props
                if new_required:
                    params["required"] = new_required
                else:
                    params.pop("required", None)

                fn["parameters"] = params

            t["function"] = fn

        # ---- 4) Deduplicate by function name if present
        try:
            fname = t.get("function", {}).get("name")
            if fname:
                if fname in seen:
                    continue
                seen.add(fname)
        except Exception:
            pass

        out.append(t)

    return out



class MedSchoolEnv(MultiTurnEnv):
    """
    Multi-turn environment that:
      • Starts a new sandbox container per task (one rollout) via SessionManager
      • Exposes tools from a registry (derived from configs/tools.yaml) + optional extras
      • Routes tool calls into the live container using per-session clients
      • Tears the container down when the rollout completes or errors

    Compatibility: conforms to verifiers' MultiTurnEnv contract.
    Stopping condition: last assistant turn without any tool_calls.
    """

    def __init__(
        self,
        dataset: Dataset,
        parser: Parser,
        rubric: Rubric,
        system_prompt: str,
        tools: List[Callable] | None = None,
        max_turns: int = 10,
        container_limit: Optional[int] = None,
        **kwargs: Any,
    ):
        # Session + tool registry
        self.session_manager = SessionManager()
        self.max_turns = max_turns

        # ---- tool exposure strategy (MERGED) ----
        # 1) Registry tools (from ToolManager; controlled by configs/tools.yaml)
        registry_oai_tools = self.session_manager.tools.as_oai_tools()

        # 2) Optional extra/manual tools supplied by caller (added, not replaced)
        self._tools: List[Callable[..., Any]] = list(tools or [])
        self._tool_map: Dict[str, Callable[..., Any]] = {fn.__name__: fn for fn in self._tools}
        manual_oai_tools = [convert_func_to_oai_tool(fn) for fn in self._tools]

        # 3) Merge + sanitize:
        #    - drop any 'strict' flags
        #    - hide 'session_id' from parameter schemas (we auto-inject it)
        merged_oai_tools = _sanitize_oai_tools(registry_oai_tools + manual_oai_tools)

        # ---- concurrency cap for container allocation ----
        settings = get_settings()
        limit = int(container_limit) if container_limit is not None else int(settings.sandbox.max_concurrent_sessions or 1)
        if limit < 1:
            limit = 1
        self._container_sem = asyncio.Semaphore(limit)

        # Up-call into MultiTurnEnv/Environment
        super().__init__(
            oai_tools=merged_oai_tools,
            max_turns=max_turns,
            dataset=dataset,
            parser=parser,
            rubric=rubric,
            system_prompt=system_prompt,
            **kwargs,
        )

    # ---------------- Termination rule ----------------

    async def is_completed(self, messages: Messages, state: State, **kwargs: Any) -> bool:
        """
        We consider a rollout complete when the latest assistant message
        does NOT contain any tool_calls (i.e., the model produced a final answer).
        """
        if not messages:
            return False
        last = messages[-1]
        if last.get("role") != "assistant":
            return False
        # If tool_calls is missing or empty → done
        tc = last.get("tool_calls")
        return (tc is None) or (tc == [])

    # ---------------- Tool routing ----------------

    async def call_tool(
        self,
        tool_name: str,
        tool_args: dict,
        tool_call_id: str,
        *,
        session_id: str,
        **kwargs: Any,
    ) -> Message:
        """
        Execute a single tool call inside the active session.
        Returns a tool message with the JSON-serialized tool result.
        """
        args = dict(tool_args or {})

        # Auto-inject the current session_id so models never have to supply it.
        args.setdefault("session_id", session_id)

        # Prefer manual tool override if provided; else use registry ToolManager
        fn = self._tool_map.get(tool_name)
        if fn is None:
            try:
                fn = self.session_manager.tools.get_callable(tool_name)
            except Exception as e:
                return {
                    "role": "tool",
                    "content": json.dumps({"error": str(e)}),
                    "tool_call_id": tool_call_id,
                }

        try:
            result = await maybe_await(fn, **args)
        except Exception as e:
            # Surface a readable tool error back to the model
            result = {"error": f"{type(e).__name__}: {str(e)}"}

        content = result if isinstance(result, str) else json.dumps(result)
        return {"role": "tool", "content": content, "tool_call_id": tool_call_id}

    async def env_response(self, messages: Messages, state: State, **kwargs: Any) -> Tuple[Messages, State]:
        """
        Given an assistant turn with tool_calls, dispatch each call and
        return the corresponding tool messages (in order).
        """
        assert messages and messages[-1].get("tool_calls"), "env_response called without tool calls"

        info = state.get("info") if isinstance(state, dict) else {}

        session_id = (info or {}).get("session_id")
        if not session_id:
            # Graceful error response for missing session_id
            first_id = ""
            tcs = messages[-1].get("tool_calls") or []
            if tcs:
                # tool_calls can be pydantic objects; be defensive
                try:
                    first_id = getattr(tcs[0], "id", "") or tcs[0].get("id", "")
                except Exception:
                    first_id = ""
            return [{
                "role": "tool",
                "content": json.dumps({"error": "No active session_id; cannot execute tools."}),
                "tool_call_id": first_id,
            }], state

        # Execute all tool calls and return their tool messages
        tool_messages: List[Message] = []
        for tool_call in messages[-1]["tool_calls"]:
            # Support either typed or dict-like tool call objects
            if isinstance(tool_call, ChatCompletionMessageToolCall):
                tool_name: str = tool_call.function.name
                try:
                    tool_args: dict = json.loads(tool_call.function.arguments or "{}")
                except Exception:
                    tool_args = {}
                tool_call_id: str = tool_call.id or ""
            else:
                # dict-like fallback
                fn_node = (tool_call or {}).get("function", {})  # type: ignore
                tool_name = fn_node.get("name", "")
                try:
                    tool_args = json.loads(fn_node.get("arguments") or "{}")
                except Exception:
                    tool_args = {}
                tool_call_id = (tool_call or {}).get("id", "")  # type: ignore

            tool_messages.append(
                await self.call_tool(tool_name, tool_args, tool_call_id, session_id=session_id)
            )
        return tool_messages, state

    # ---------------- Per-task container lifecycle ----------------

    async def rollout(
        self,
        client,
        model: str,
        prompt: Messages,
        answer: str = "",
        task: str = "default",
        info: Optional[Info] = None,
        sampling_args: Optional[SamplingArgs] = None,
        **kwargs: Any,
    ) -> Tuple[Messages, State]:
        """
        One rollout == one task:
          • Acquire capacity
          • Start a fresh session (container)
          • Run the interactive loop (model <-> env tools)
          • Tear down the session
        """
        async with self._container_sem:
            # Optional task bookkeeping hook (compatible with your SessionManager)
            task_obj = self.session_manager.task_manager.next_task()
            # Force a brand-new container per example by making the request unique.
            # Many middleman APIs will reuse an "active" session if the create payload is identical.
            # A per-rollout nonce in env guarantees a fresh container.
            rollout_id = str(uuid.uuid4())
            ctx = await self.session_manager.start_session(
                task=task_obj,
                env={"ROLLOUT_ID": rollout_id, "TASK_LABEL": str(task or "")}
            )
            session_id = ctx.session_id

            # Seed the info handed to Environment so tool schemas get exposed
            seeded_info: Info = dict(info or {})
            # Ensure the merged/sanitized tool schemas are actually attached
            if self.oai_tools:
                # Re-sanitize here defensively in case upstream code mutated the list
                seeded_info["oai_tools"] = _sanitize_oai_tools(self.oai_tools)
            seeded_info["session_id"] = session_id

            try:
                return await super().rollout(
                    client=client,
                    model=model,
                    prompt=prompt,
                    answer=answer,
                    task=task,
                    info=seeded_info,
                    sampling_args=sampling_args,
                    **kwargs,
                )
            finally:
                # Always tear the container down, even on exceptions or max_turns stop
                try:
                    await self.session_manager.stop_session(session_id)
                except Exception:
                    # Best-effort cleanup; swallow errors to avoid masking primary failures
                    pass
