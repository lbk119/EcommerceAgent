"""deepagents-native 主运行时构建与调用。

本文件把模型、tools、subagents、middleware、memory、backend、filesystem、HITL interrupt 和 checkpointer
组装成 `create_deep_agent` 图。AgentRuntime 只调用这里，不直接了解 deepagents 的具体参数组合。
"""

from __future__ import annotations

import os
import time
from typing import Any

from agent.plan.models import AgentTaskPlan
from agent.subagent.config import deepagents_enabled, get_deepagents_profile
from agent.subagent.filesystem import build_composite_backend, filesystem_permissions
from agent.subagent.guard import GuardViolation, RuntimeGuard, guard_middleware, reset_active_runtime_guard, set_active_runtime_guard
from agent.subagent.hitl import interrupt_on_for_profile
from agent.memory import MemoryBackendConfigurationError, MemoryBackendFactory
from agent.subagent.mcp import mcp_policy_for_profile
from agent.subagent.subagents import build_deepagents_subagents, get_subagent_specs
from agent.subagent.tools import reset_tool_context, set_tool_context
from agent.trace.tracer import tracer
from agent.plan.planner import PLANNER_PROMPT
from agent.runtime.execution_result import ExecutionResult


_NATIVE_AGENT_CACHE: dict[str, Any] = {}
_NATIVE_SPEC_CACHE: dict[str, list[Any]] = {}


def build_native_main_agent(profile: str):
    """按 profile 构建 deepagents main agent，并延迟初始化重依赖。"""
    config = get_deepagents_profile(profile)
    from deepagents import create_deep_agent
    from agent.tools.registry import MAIN_AGENT_PERMISSIONS, MAIN_AGENT_TOOLS, tool_registry
    from agent.llm import get_deep_model, get_fast_model, get_standard_model

    store = None
    memory_files = None
    backend = None
    if config.allow_memory_write and os.getenv("ENABLE_DEEPAGENTS_MEMORY", "true").lower() in {"1", "true", "yes", "on"}:
        try:
            memory_backend = MemoryBackendFactory().build(production=os.getenv("APP_ENV", "dev").lower() in {"prod", "production"})
            store = memory_backend.store
            memory_files = ["/memories/preferences.md", "/memories/shop_strategy.md", "/policies/compliance.md"] if store is not None else None
            backend = build_composite_backend(config.name, store=store)
            if memory_backend.warning:
                print(f"[DeepAgentsMemory] {memory_backend.warning}")
        except MemoryBackendConfigurationError as error:
            if os.getenv("APP_ENV", "dev").lower() in {"prod", "production"}:
                raise
            print(f"[DeepAgentsMemory] degraded local startup: {error}")
    if backend is None and config.allow_filesystem:
        backend = build_composite_backend(config.name)

    checkpointer = _checkpointer_for_profile(config.name)
    if config.name == "realtime":
        model = get_fast_model()
        tools = []
    else:
        model = get_deep_model() if config.name == "deep" else get_standard_model()
        tools = tool_registry.tools(MAIN_AGENT_TOOLS, MAIN_AGENT_PERMISSIONS, "deepagents_main_agent")
    subagents = build_deepagents_subagents(config.name)
    _NATIVE_SPEC_CACHE[config.name] = get_subagent_specs(config.name)
    middleware = guard_middleware() if config.enable_guard else []
    return create_deep_agent(
        model=model,
        tools=tools,
        middleware=middleware,
        subagents=subagents,
        system_prompt=main_system_prompt(config.name),
        memory=memory_files,
        permissions=filesystem_permissions(config.name),
        backend=backend,
        interrupt_on=interrupt_on_for_profile(config.name),
        checkpointer=checkpointer,
        store=store,
        name=f"ecommerce_{config.name}_main_agent",
    )


def get_native_main_agent(profile: str):
    """返回缓存后的 native main agent 和当前 profile 的 subagent specs。"""
    config = get_deepagents_profile(profile)
    if config.name not in _NATIVE_AGENT_CACHE:
        _NATIVE_AGENT_CACHE[config.name] = build_native_main_agent(config.name)
    return _NATIVE_AGENT_CACHE[config.name], _NATIVE_SPEC_CACHE.get(config.name, [])


def clear_native_agent_cache() -> None:
    """清空 native agent 缓存，让策略、store 或工具配置变更在下次构建时生效。"""
    _NATIVE_AGENT_CACHE.clear()
    _NATIVE_SPEC_CACHE.clear()


def main_system_prompt(profile: str) -> str:
    """生成 main agent system prompt，合并 Planner 策略、profile 限制和已批准治理策略。"""
    config = get_deepagents_profile(profile)
    mcp_policy = mcp_policy_for_profile(profile)
    if config.name == "realtime":
        return "\n\n".join([
            "You are EcommerceAgent realtime main agent, built with deepagents but running with no tools and no subagents.",
            "Reply quickly and do not call tools, browse, write files, query databases, or delegate to subagents.",
            "Use the provided PlannerAgent plan only as routing context. If the plan has business assignments, explain that the question needs a background standard job and summarize the recognized intent. Do not pretend you executed business analysis.",
            "If the plan requires clarification, ask the clarification questions directly.",
            "If the plan is out of scope, give a clear capability boundary and suggest ecommerce questions the user can ask.",
            "For ordinary chat or simple guidance, answer naturally and briefly.",
            "Always be honest about missing data and never invent tool results.",
        ])
    prompt_parts = [
        PLANNER_PROMPT,
        "你现在是 deepagents main agent / orchestrator。必须通过 deepagents subagents delegation 和已注册工具执行业务分析。",
        "Main Agent 职责：理解需求、选择 subagents、判断依赖顺序、把上游结果传给下游、汇总最终业务结论。",
        "禁止：自己写 SQL、绕过 schema、绕过 profile 限制、在未审批时执行高风险动作、编造工具结果、无限重复调用同一工具或 subagent。",
        f"runtime_profile: {config.name}",
        f"available_subagents: {', '.join(config.subagents)}",
        f"network_search_allowed: {config.allow_network_search}; database_query_allowed: {config.allow_database_query}; mcp_status: {mcp_policy.status}",
        "有数据依赖时串行委托并把上游结果摘要传给下游；无依赖时可以独立委托。最终回答必须说明证据、风险、缺失数据和下一步动作。",
    ]
    policy_instructions = _approved_policy_instructions()
    if policy_instructions:
        prompt_parts.append("已批准的长期策略指令：\n" + "\n".join(f"- {item}" for item in policy_instructions))
    return "\n\n".join(prompt_parts)


def _approved_policy_instructions() -> list[str]:
    try:
        from agent.reflection.policy_review import list_approved_policy_instructions

        return list_approved_policy_instructions()
    except Exception:
        return []


class DeepAgentsNativeRuntime:
    """AgentRuntime 调用 deepagents 图的薄包装。"""

    def __init__(self, agent: Any):
        self.agent = agent

    async def run(self, context: Any, sanitized_query: str, task_plan: AgentTaskPlan) -> ExecutionResult:
        profile = str(context.config.get("configurable", {}).get("runtime_profile") or task_plan.profile)
        if not deepagents_enabled(profile):
            raise RuntimeError(f"deepagents native runtime disabled for profile {profile}")
        guard = RuntimeGuard.for_profile(profile, trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id)
        started = time.perf_counter()
        tool_token = set_tool_context({
            "tenant_id": context.identity.tenant_id,
            "shop_id": context.identity.shop_id,
            "user_id": context.identity.user_id,
            "task_id": context.task_id,
            "conversation_id": context.conversation_id,
            "profile": profile,
            "agent_id": "deepagents_main_agent",
            "sandbox_timeout_seconds": get_deepagents_profile(profile).sandbox_timeout_seconds,
            "sandbox_memory_mb": get_deepagents_profile(profile).sandbox_memory_mb,
            "sandbox_cpu_count": get_deepagents_profile(profile).sandbox_cpu_count,
            "guard": guard,
        })
        guard_token = set_active_runtime_guard(guard)
        try:
            prompt = _build_user_prompt(sanitized_query, task_plan, context.path_instruction)
            tracer.emit("deepagents_native_started", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="deepagents_main_agent", metadata={"profile": profile, "subagents": [item.name for item in get_subagent_specs(profile)]})
            output = await self.agent.ainvoke({"messages": [{"role": "user", "content": prompt}]}, config={**context.config, "recursion_limit": get_deepagents_profile(profile).recursion_limit})
            content = _extract_content(output)
            guard.record_assistant_message(content)
            tracer.emit("deepagents_native_finished", trace_id=context.task_id, task_id=context.task_id, conversation_id=context.conversation_id, agent_name="deepagents_main_agent", latency_ms=round((time.perf_counter() - started) * 1000, 2), metadata={"profile": profile, "guard": guard.snapshot()})
            return ExecutionResult(content=content, source="deepagents_native", workflow_name="deepagents_main_agent", workflow_definition={"runtime": "deepagents_native", "task_plan": task_plan.to_dict()}, structured_result={"conclusion": content, "planSummary": task_plan.to_dict(), "runtime": "deepagents_native", "profile": profile})
        except GuardViolation as error:
            content = f"## 已降级停止\nAgentSafetyMiddleware 已阻断继续执行，原因：{error.reason}。"
            return ExecutionResult(content=content, source="deepagents_native_degraded", workflow_name="agent_safety_middleware", workflow_definition={"guard": error.findings, "task_plan": task_plan.to_dict()}, structured_result={"conclusion": content, "degraded": True, "loopGuard": error.findings})
        finally:
            reset_active_runtime_guard(guard_token)
            reset_tool_context(tool_token)


def _build_user_prompt(query: str, task_plan: AgentTaskPlan, path_instruction: str) -> str:
    return "\n\n".join([
        f"用户请求：{query}",
        "PlannerAgent 兼容计划（用于 trace 和调度参考，不是工具结果）：",
        str(task_plan.to_dict()),
        path_instruction,
    ])


def _extract_content(output: Any) -> str:
    if isinstance(output, dict):
        messages = output.get("messages") or []
        if messages:
            last = messages[-1]
            content = getattr(last, "content", None) if not isinstance(last, dict) else last.get("content")
            if content:
                return str(content)
        if output.get("output"):
            return str(output["output"])
    return str(output)


def _checkpointer_for_profile(profile: str):
    if profile == "realtime":
        return None
    if profile == "deep" or os.getenv("ENABLE_DEEPAGENTS_HITL", "true").lower() in {"1", "true", "yes", "on"}:
        from agent.subagent.checkpoint import build_checkpointer

        return build_checkpointer()
    return None


