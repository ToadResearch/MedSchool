# based on
#   - MultiTurnEnv: https://github.com/willccbb/verifiers/blob/d66990cc07f126ea35abc622ea60d788b7b9b9c7/verifiers/envs/multiturn_env.py
#   - ToolEnv: https://github.com/willccbb/verifiers/blob/d66990cc07f126ea35abc622ea60d788b7b9b9c7/verifiers/envs/tool_env.py
# environment/medschoolenv.py
from __future__ import annotations

from typing import Any, Callable, Optional, Tuple, Dict, List
import asyncio
import json

from verifiers.envs.multiturn_env import MultiTurnEnv
from verifiers.types import (
    ChatCompletionMessageToolCall,
    Message,
    Messages,
    State,
    Info,
    SamplingArgs,
)
from verifiers.utils.async_utils import maybe_await
from verifiers.utils.tool_utils import convert_func_to_oai_tool

from src import SessionManager, get_settings


class MedSchoolEnv(MultiTurnEnv):
    def __init__(
        self,
        *,
        tools: List[Callable] | None = None,
        max_turns: int = 10,
        container_limit: Optional[int] = None,
        error_formatter: Callable[[Exception], str] = lambda e: f"{str(e)}",
        **kwargs: Any,
    ):
        self.session_manager = SessionManager()
        self.max_turns = max_turns
        self.error_formatter = error_formatter

        # --- tool exposure strategy ---
        self._manual_tools: bool = tools is not None
        if self._manual_tools:
            # Use the callables passed in
            self._tools = list(tools or [])
            self._tool_map: Dict[str, Callable[..., Any]] = {fn.__name__: fn for fn in self._tools}
            oai_tools = [convert_func_to_oai_tool(fn) for fn in self._tools]
        else:
            # Use ToolManager registry (single source of truth from configs/tools.yaml)
            oai_tools = self.session_manager.tools.as_oai_tools()

        # --- container cap (separate from verifiers' rollout concurrency) ---
        settings = get_settings()
        limit = int(container_limit) if container_limit is not None else int(
            settings.sandbox.max_concurrent_sessions or 1
        )
        if limit < 1:
            limit = 1
        self._container_sem = asyncio.Semaphore(limit)

        super().__init__(oai_tools=oai_tools, max_turns=max_turns, **kwargs)

    # -------- termination (same as ToolEnv) --------
    async def is_completed(self, messages: Messages, state: State, **kwargs: Any) -> bool:
        assert isinstance(messages, list)
        is_assistant_message = messages[-1]["role"] == "assistant"
        no_tool_calls = ("tool_calls" not in messages[-1]) or (messages[-1]["tool_calls"] is None)
        return is_assistant_message and no_tool_calls

    # -------- tool execution (inject session_id; dispatch manual vs registry) --------
    async def call_tool(
        self,
        tool_name: str,
        tool_args: dict,
        tool_call_id: str,
        *,
        session_id: str,
        **kwargs: Any,
    ) -> Message:
        try:
            args = dict(tool_args or {})
            args.setdefault("session_id", session_id)

            if self._manual_tools:
                fn = self._tool_map[tool_name]
            else:
                fn = self.session_manager.tools.get_callable(tool_name)

            result = await maybe_await(fn, **args)
            content = json.dumps(result) if not isinstance(result, str) else result
            return {"role": "tool", "content": content, "tool_call_id": tool_call_id}
        except Exception as e:
            return {"role": "tool", "content": self.error_formatter(e), "tool_call_id": tool_call_id}

    async def env_response(self, messages: Messages, state: State, **kwargs: Any) -> Tuple[Messages, State]:
        assert isinstance(messages, list)
        assert "tool_calls" in messages[-1]

        info = state.get("info", {}) if isinstance(state, dict) else {}
        session_id = info.get("session_id")
        if not session_id:
            first_id = ""
            if messages[-1].get("tool_calls"):
                first_id = messages[-1]["tool_calls"][0].id or ""
            err = {"role": "tool", "content": "No active session_id; cannot execute tools.", "tool_call_id": first_id}
            return [err], state

        tool_messages: List[Message] = []
        for tool_call in messages[-1]["tool_calls"]:
            assert isinstance(tool_call, ChatCompletionMessageToolCall)
            tool_name: str = tool_call.function.name
            try:
                tool_args: dict = json.loads(tool_call.function.arguments or "{}")
            except Exception:
                tool_args = {}
            tool_call_id: str = tool_call.id or ""
            tool_messages.append(
                await self.call_tool(tool_name, tool_args, tool_call_id, session_id=session_id)
            )
        return tool_messages, state

    # -------- per-rollout container lifecycle --------
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
        async with self._container_sem:
            task_obj = self.session_manager.task_manager.next_task()  # optional bookkeeping
            ctx = await self.session_manager.start_session(task=task_obj)
            session_id = ctx.session_id

            seeded_info: Info = dict(info or {})
            if self.oai_tools and "oai_tools" not in seeded_info:
                seeded_info["oai_tools"] = self.oai_tools
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
                try:
                    await self.session_manager.stop_session(session_id)
                except Exception:
                    pass
