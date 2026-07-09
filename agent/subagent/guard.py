"""AgentSafetyMiddleware 与运行时循环保护。

本文件同时提供可被 deepagents middleware 调用的 guard，以及工具 wrapper 可以读取的 ContextVar。
它记录模型、工具、subagent 调用次数和重复模式，触发后写 `agent_loop_guard_triggered` trace 并停止执行。
"""

from __future__ import annotations

import json
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from agent.subagent.config import DeepAgentsProfileConfig, get_deepagents_profile
from agent.trace.tracer import tracer


class GuardViolation(RuntimeError):
    """运行时 guard 触发后的降级异常，包含原因和审计 findings。"""

    def __init__(self, reason: str, findings: dict[str, Any]):
        super().__init__(reason)
        self.reason = reason
        self.findings = findings


_ACTIVE_RUNTIME_GUARD: ContextVar["RuntimeGuard | None"] = ContextVar("deepagents_active_runtime_guard", default=None)


def set_active_runtime_guard(guard: "RuntimeGuard"):
    return _ACTIVE_RUNTIME_GUARD.set(guard)


def reset_active_runtime_guard(token: Any) -> None:
    _ACTIVE_RUNTIME_GUARD.reset(token)


def current_runtime_guard() -> "RuntimeGuard | None":
    return _ACTIVE_RUNTIME_GUARD.get()


@dataclass
class RuntimeGuard:
    """单次任务的预算、重复调用和空进展检测器。"""

    profile: DeepAgentsProfileConfig
    trace_id: str = ""
    task_id: str = ""
    conversation_id: str = ""
    started_at: float = field(default_factory=time.perf_counter)
    model_calls: int = 0
    tool_calls: int = 0
    subagent_calls: int = 0
    tool_counts: dict[str, int] = field(default_factory=dict)
    subagent_counts: dict[str, int] = field(default_factory=dict)
    tool_args_counts: dict[str, int] = field(default_factory=dict)
    last_assistant_message: str = ""
    repeated_assistant_messages: int = 0

    @classmethod
    def for_profile(cls, profile: str, *, trace_id: str = "", task_id: str = "", conversation_id: str = "") -> "RuntimeGuard":
        return cls(get_deepagents_profile(profile), trace_id=trace_id, task_id=task_id, conversation_id=conversation_id)

    def check_runtime(self) -> None:
        if time.perf_counter() - self.started_at > self.profile.max_runtime_seconds:
            self._trigger("timeout")

    def record_model_call(self) -> None:
        self.model_calls += 1
        if self.model_calls > self.profile.max_model_calls:
            self._trigger("budget_exceeded", {"budget": "max_model_calls"})

    def record_tool_call(self, tool_name: str, args: dict[str, Any] | None = None) -> None:
        self.check_runtime()
        self.tool_calls += 1
        self.tool_counts[tool_name] = self.tool_counts.get(tool_name, 0) + 1
        args_key = f"{tool_name}:{json.dumps(args or {}, ensure_ascii=True, sort_keys=True, default=str)}"
        self.tool_args_counts[args_key] = self.tool_args_counts.get(args_key, 0) + 1
        if self.tool_calls > self.profile.max_tool_calls:
            self._trigger("budget_exceeded", {"budget": "max_tool_calls"})
        if self.tool_args_counts[args_key] > self.profile.same_tool_same_args_limit:
            self._trigger("tool_loop", {"tool_name": tool_name})

    def record_subagent_call(self, subagent_name: str) -> None:
        self.check_runtime()
        self.subagent_calls += 1
        self.subagent_counts[subagent_name] = self.subagent_counts.get(subagent_name, 0) + 1
        if self.subagent_calls > self.profile.max_subagent_calls:
            self._trigger("budget_exceeded", {"budget": "max_subagent_calls"})
        if self.subagent_counts[subagent_name] > self.profile.same_subagent_limit:
            self._trigger("subagent_loop", {"subagent_name": subagent_name})

    def record_assistant_message(self, content: str) -> None:
        normalized = " ".join(content.lower().split())[:500]
        if normalized and normalized == self.last_assistant_message:
            self.repeated_assistant_messages += 1
        else:
            self.repeated_assistant_messages = 0
            self.last_assistant_message = normalized
        if self.repeated_assistant_messages >= 2:
            self._trigger("no_progress")

    def snapshot(self) -> dict[str, Any]:
        return {
            "profile": self.profile.name,
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "subagent_calls": self.subagent_calls,
            "tool_counts": self.tool_counts,
            "subagent_counts": self.subagent_counts,
            "elapsed_seconds": round(time.perf_counter() - self.started_at, 3),
        }

    def _trigger(self, reason: str, extra: dict[str, Any] | None = None) -> None:
        findings = {**self.snapshot(), **(extra or {}), "reason": reason}
        tracer.emit(
            "agent_loop_guard_triggered",
            trace_id=self.trace_id,
            task_id=self.task_id,
            conversation_id=self.conversation_id,
            agent_name="agent_safety_middleware",
            metadata=findings,
        )
        raise GuardViolation(reason, findings)


class AgentExecutionGuardMiddleware(AgentMiddleware):
    """挂到 deepagents 的 middleware，用于在模型/工具调用前记录预算。"""

    """LangChain/DeepAgents middleware bridge for per-request safety budgets."""

    name = "agent_execution_guard"

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        guard = current_runtime_guard()
        if guard is not None:
            guard.check_runtime()
        return None

    def after_agent(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        guard = current_runtime_guard()
        if guard is not None:
            guard.check_runtime()
        return None

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        guard = current_runtime_guard()
        if guard is not None:
            guard.record_model_call()
        return handler(request)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        guard = current_runtime_guard()
        if guard is not None:
            guard.record_model_call()
        return await handler(request)

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        _record_middleware_tool_call(request)
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        _record_middleware_tool_call(request)
        return await handler(request)


def guard_middleware() -> list[AgentExecutionGuardMiddleware]:
    return [AgentExecutionGuardMiddleware()]


def _record_middleware_tool_call(request: Any) -> None:
    guard = current_runtime_guard()
    if guard is None:
        return
    tool_name = _tool_name_from_request(request)
    if not tool_name:
        guard.check_runtime()
        return
    if tool_name in {"product_analysis", "inventory", "campaign", "report", "data_quality", "knowledge_base", "network_search", "database_query"}:
        guard.record_subagent_call(tool_name)
    else:
        guard.record_tool_call(tool_name, _tool_args_from_request(request))


def _tool_name_from_request(request: Any) -> str:
    tool_call = getattr(request, "tool_call", None) or getattr(request, "tool", None)
    if isinstance(tool_call, dict):
        return str(tool_call.get("name") or "")
    return str(getattr(tool_call, "name", "") or getattr(request, "name", "") or "")


def _tool_args_from_request(request: Any) -> dict[str, Any]:
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, dict):
        args = tool_call.get("args") or tool_call.get("arguments") or {}
        return dict(args) if isinstance(args, dict) else {"args": str(args)[:500]}
    args = getattr(request, "args", None)
    return dict(args) if isinstance(args, dict) else {}

