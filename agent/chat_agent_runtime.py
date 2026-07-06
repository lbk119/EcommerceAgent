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
from agent.planning.task_classifier import TaskClassification, classify_task
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
            classification=_classification_from_payload(payload) or classify_task(raw_question),
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
    classification: TaskClassification,
    conversation_id: str,
    task_id: str,
    message_id: str,
    tenant_id: str,
    user_id: str,
    shop_id: str,
) -> str:
    stage_started = time.perf_counter()
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

    tracer.emit(
        "task_classified",
        trace_id=task_id,
        task_id=task_id,
        conversation_id=conversation_id,
        agent_name="chat_task_classifier",
        metadata={"stage": "task_classification", "status": "completed", "task_classification": classification.to_dict(), "classified_from": "raw_user_question"},
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
        metadata={"query": redact_secrets(guard_result.sanitized_query), "task_classification": classification.to_dict(), "runtime_profile": "chat_lightweight"},
    )

    if classification.preferred_workflow != "deterministic_dag":
        final_text = _boundary_answer(guard_result.sanitized_query, classification)
        monitor.emit_assistant_delta(task_id=task_id, conversation_id=conversation_id, message_id=message_id, delta=final_text)
        tracer.emit("agent_finished", trace_id=task_id, task_id=task_id, conversation_id=conversation_id, agent_name="chat_agent_runtime", metadata={"stage": "agent_finished", "status": "completed", "source": "boundary"})
        return final_text

    async def no_deepagent_fallback(_: str) -> str:
        # ChatAgentRuntime 不允许同步拉起 DeepAgent；未覆盖任务应给边界说明或由显式后台任务承接。
        return _boundary_answer(guard_result.sanitized_query, classification)

    result: ExecutionResult = await WorkflowRunner().run_or_fallback(
        query=guard_result.sanitized_query,
        classification=classification,
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


def _classification_from_payload(payload: dict[str, Any]) -> TaskClassification | None:
    data = payload.get("classification")
    if not isinstance(data, dict):
        return None
    task_type = str(data.get("task_type") or "general_business_chat")
    risk = str(data.get("risk") or "low")
    preferred_workflow = str(data.get("preferred_workflow") or "deepagent")
    return TaskClassification(task_type=task_type, risk=risk, requires_critic=bool(data.get("requires_critic", False)), preferred_workflow=preferred_workflow)


def _boundary_answer(question: str, classification: TaskClassification) -> str:
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
        f"这条问题被识别为 `{classification.task_type}`，当前 AI Chat 只同步执行高频经营 workflow。\n\n"
        "## 建议问法\n"
        "- 最近爆品有哪些？\n"
        "- 哪个商品最值得优化？\n"
        "- 库存风险优先级是什么？\n"
        "- 这个季节适合卖什么东西？"
    )