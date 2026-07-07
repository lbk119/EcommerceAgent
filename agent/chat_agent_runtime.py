"""AI Chat 专用轻量运行时。

这个模块刻意不导入 agent.main_agent，也不构建 DeepAgent、subagents、checkpointer、Critic 或长期记忆写入。
它只服务用户可见的聊天入口：原始问题安全检查、原始问题分类、固定业务 workflow、草稿流式推送、
fast model 轻润色，以及无法覆盖时的边界说明。
"""

from __future__ import annotations

import time
from typing import Any

from agent.memory.schema import MemoryIdentity
from agent.observability.tracer import tracer
from agent.planning.planner_agent import planner_agent
from agent.planning.schemas import TaskPlan
from agent.runtime.execution_result import ExecutionResult
from agent.security.prompt_guard import inspect_user_prompt
from agent.security.redaction import redact_secrets
from agent.workflows.workflow_runner import WorkflowRunner
from api.context import reset_identity_context, reset_thread_context, set_identity_context, set_thread_context
from api.monitor import monitor


async def run_chat_agent(payload: dict[str, Any]) -> str:
    """执行 AI Chat 轻量链路，并返回最终可持久化的 assistant 文本。

    payload 必须带 raw_user_question。历史兼容场景下如果缺失，就回退到 query，但分类永远优先使用
    raw_user_question，避免包装 prompt 中的“商品、库存、活动、报告”等词污染分类器。
    """
    raw_question = str(payload.get("raw_user_question") or payload.get("query") or "").strip()
    conversation_id = str(payload.get("conversation_id") or payload.get("thread_id") or "")
    task_id = str(payload.get("task_id") or "")
    message_id = str(payload.get("message_id") or "")
    tenant_id = str(payload.get("tenant_id") or "default_tenant")
    user_id = str(payload.get("user_id") or "local_user")
    shop_id = str(payload.get("shop_id") or "default_shop")

    thread_token = set_thread_context(conversation_id)
    identity_token = set_identity_context(MemoryIdentity(tenant_id=tenant_id, user_id=user_id, shop_id=shop_id, conversation_id=conversation_id, task_id=task_id))
    try:
        return await _run_chat_agent_inner(
            raw_question=raw_question,
            conversation_id=conversation_id,
            task_id=task_id,
            message_id=message_id,
            tenant_id=tenant_id,
            user_id=user_id,
            shop_id=shop_id,
        )
    finally:
        reset_identity_context(identity_token)
        reset_thread_context(thread_token)


async def _run_chat_agent_inner(
    *,
    raw_question: str,
    conversation_id: str,
    task_id: str,
    message_id: str,
    tenant_id: str,
    user_id: str,
    shop_id: str,
) -> str:
    """AI Chat 的真实执行主体。

    该函数只跑轻量可控链路：安全检查 -> 分类 trace -> 固定 DAG workflow -> 结果汇总。
    它不会同步启动 DeepAgent，也不会写长期记忆，保证前端聊天可以快速完成或明确给出能力边界。
    """
    stage_started = time.perf_counter()
    # 先把安全检查写入 trace，前端 timeline 可以看到任务不是“卡住”，而是在做 prompt guard。
    tracer.emit("prompt_guard_started", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="chat_prompt_guard", metadata={"stage": "prompt_guard", "status": "running"})
    guard_result = inspect_user_prompt(raw_question)
    tracer.emit(
        "prompt_guard_finished",
        trace_id=task_id,
        task_id=task_id,
        conversation_id=conversation_id,
        agent_name="chat_prompt_guard",
        latency_ms=round((time.perf_counter() - stage_started) * 1000, 2),
        metadata={"stage": "prompt_guard", "status": "completed", "prompt_guard": guard_result.to_metadata()},
    )
    task_plan = await planner_agent.plan_async(guard_result.sanitized_query, profile="realtime", context={"tenant_id": tenant_id, "shop_id": shop_id, "user_id": user_id}, trace_id=task_id, task_id=task_id, conversation_id=conversation_id)
    tracer.emit(
        "runtime_stage_completed",
        trace_id=task_id,
        task_id=task_id,
        conversation_id=conversation_id,
        agent_name="chat_agent_runtime",
        metadata={"stage": "planner_acceptance_fallback", "status": "completed", "plan_id": task_plan.plan_id},
    )
    tracer.emit(
        "task_classified",
        trace_id=task_id,
        task_id=task_id,
        conversation_id=conversation_id,
        agent_name="chat_planner_agent",
        metadata={"stage": "task_planning", "status": "completed", **task_plan.to_trace_metadata()},
    )
    tracer.emit(
        "context_prepared",
        trace_id=task_id,
        task_id=task_id,
        conversation_id=conversation_id,
        agent_name="chat_agent_runtime",
        metadata={"stage": "context_prepared", "status": "completed", "tenant_id": tenant_id, "user_id": user_id, "shop_id": shop_id},
    )
    tracer.emit(
        "agent_started",
        trace_id=task_id,
        task_id=task_id,
        conversation_id=conversation_id,
        agent_name="chat_agent_runtime",
        metadata={"query": redact_secrets(guard_result.sanitized_query), **task_plan.to_trace_metadata(include_plan=False), "runtime_profile": "chat_lightweight"},
    )

    if task_plan.execution_mode not in {"deterministic_dag", "hybrid_plan", "boundary"}:
        # AI Chat 只同步处理确定性经营 workflow。其它问题给边界说明，不在用户等待时拉起 DeepAgent。
        final_text = _boundary_answer(guard_result.sanitized_query, task_plan)
        monitor.emit_assistant_delta(task_id=task_id, conversation_id=conversation_id, message_id=message_id, delta=final_text)
        tracer.emit("agent_finished", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="chat_agent_runtime", metadata={"stage": "agent_finished", "status": "completed", "source": "boundary"})
        return final_text

    async def no_deepagent_fallback(_: str) -> str:
        # ChatAgentRuntime 不允许同步拉起 DeepAgent；未覆盖任务应给边界说明或由显式后台任务承接。
        return _boundary_answer(guard_result.sanitized_query, task_plan)

    result: ExecutionResult = await WorkflowRunner().run_or_fallback(
        query=guard_result.sanitized_query,
        task_plan=task_plan,
        trace_id=task_id,
        task_id=task_id,
        conversation_id=conversation_id,
        fallback=no_deepagent_fallback,
        allow_deepagent_fallback=False,
        message_id=message_id,
        runtime_profile="realtime",
    )
    tracer.emit("critic_skipped", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="chat_agent_runtime", metadata={"stage": "critic", "status": "skipped", "reason": "chat_lightweight_runtime"})
    tracer.emit("memory_write_skipped", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="chat_agent_runtime", metadata={"stage": "memory_write", "status": "skipped", "reason": "chat_lightweight_runtime"})
    tracer.emit("agent_finished", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="chat_agent_runtime", metadata={"stage": "agent_finished", "status": "completed", "source": result.source, "workflow_name": result.workflow_name})
    return result.to_final_result()


def _boundary_answer(question: str, task_plan: TaskPlan) -> str:
    """普通闲聊或未接入能力不进 DeepAgent，先明确边界，再给可继续的业务入口。"""
    if any(keyword in question for keyword in ("天气", "气温", "下雨", "空气质量")):
        return (
            "## 当前能力边界\n"
            "我现在没有接入实时天气、地理位置或外部搜索数据，所以不能可靠回答天气。\n\n"
            "## 我可以继续帮你做\n"
            "- 基于当前店铺订单、商品、库存和活动数据给出选品建议。\n"
            "- 如果你想看应季经营机会，可以直接问：这个季节适合卖什么东西？"
        )
    return (
        "## 当前能力边界\n"
        f"这条问题被 PlannerAgent 规划为 `{task_plan.primary_task_type}`，当前 AI Chat 只同步执行高频经营 workflow。\n\n"
        "## 建议问法\n"
        "- 最近爆品有哪些？\n"
        "- 哪个商品最值得优化？\n"
        "- 库存风险优先级是什么？\n"
        "- 这个季节适合卖什么东西？"
    )