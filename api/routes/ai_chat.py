"""AI Chat API backed by the DeepAgents runtime queue."""

from __future__ import annotations

import os
import time
import uuid

from fastapi import APIRouter, HTTPException, Request, status

from agent.trace.reader import build_task_timeline
from agent.trace.tracer import tracer
from agent.plan.planner import planner_agent
from api.routes.helpers import gateway_identity, requested_shop
from api.services.ai_chat_service import create_chat_run, fail_chat_run, get_message, get_run_by_task, list_conversations, list_messages
from api.task_queue import task_queue
from api.task_runtime import task_runtime


router = APIRouter(prefix="/api/ai-chat", tags=["ai-chat"])


@router.post("/messages", status_code=status.HTTP_202_ACCEPTED)
async def chat(payload: dict, request: Request):
    """Accept an AI Chat message quickly and enqueue runtime execution."""
    started_at = time.perf_counter()
    identity = gateway_identity(request)
    tenant_id = identity["tenant_id"]
    shop_id = requested_shop(request, identity)
    user_id = identity["user_id"]
    user_content = str(payload.get("content") or "").strip()
    if not user_content:
        raise HTTPException(status_code=400, detail="请输入要分析的问?")

    conversation_id = str(payload.get("conversationId") or uuid.uuid4())
    task_id = str(uuid.uuid4())
    acceptance_plan = planner_agent.plan(user_content, profile="realtime")
    runtime_profile = _runtime_profile_for_chat(payload, acceptance_plan)
    task_plan = acceptance_plan if runtime_profile == "realtime" else planner_agent.plan(user_content, profile=runtime_profile)
    task_plan_payload = task_plan.to_lightweight_dict()
    task_plan_full = task_plan.to_dict()
    intent = task_plan.primary_task_type
    agent_query = build_agent_chat_query(user_content)
    run = create_chat_run(
        tenant_id=tenant_id,
        shop_id=shop_id,
        user_id=user_id,
        conversation_id=conversation_id,
        user_content=user_content,
        intent=intent,
        task_id=task_id,
    )
    metadata = {
        "conversation_id": conversation_id,
        "tenant_id": tenant_id,
        "shop_id": shop_id,
        "user_id": user_id,
        "source": "ai_chat",
        "message_id": run["message_id"],
        "intent": intent,
        "raw_user_question": user_content,
        "task_plan": task_plan_payload,
        "runtime_profile": runtime_profile,
        "max_runtime_seconds": int(os.getenv("AI_CHAT_MAX_RUNTIME_SECONDS", "180")),
    }
    try:
        await task_runtime.enqueue(task_id, user_content, metadata=metadata)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    tracer.emit(
        "queued",
        trace_id=task_id,
        task_id=task_id,
        conversation_id=conversation_id,
        agent_name="ai_chat",
        latency_ms=round((time.perf_counter() - started_at) * 1000, 2),
        metadata={"stage": "queued", "status": "running", "intent": intent, "message_id": run["message_id"], "task_plan": task_plan_payload},
    )
    await task_queue.enqueue(
        {
            "query": agent_query,
            "conversation_id": conversation_id,
            "thread_id": conversation_id,
            "task_id": task_id,
            "tenant_id": tenant_id,
            "shop_id": shop_id,
            "user_id": user_id,
            "source": "ai_chat",
            "message_id": run["message_id"],
            "intent": intent,
            "raw_user_question": user_content,
            "task_plan": task_plan_full,
            "agent_query": agent_query,
            "runtime_profile": runtime_profile,
            "model_profile": os.getenv("AI_CHAT_MODEL_PROFILE", "fast"),
            "target_seconds": int(os.getenv("AI_CHAT_TOTAL_TARGET_SECONDS", "15")),
            "accepted_at": time.time(),
        }
    )
    accepted_latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
    print(f"[AIChat] accepted tenant={tenant_id} shop={shop_id} user={user_id} task={task_id} latency_ms={accepted_latency_ms}")
    return {
        "messageId": run["message_id"],
        "conversationId": conversation_id,
        "taskId": task_id,
        "status": "running",
        "source": "agent",
        "wsThreadId": conversation_id,
        "intent": intent,
        "runtimeProfile": runtime_profile,
        "acceptedLatencyMs": accepted_latency_ms,
        "message": {
            "id": run["message_id"],
            "role": "assistant",
            "content": "Agent 已接收任务，正在进入运行队列?",
            "source": "agent",
            "status": "running",
            "taskId": task_id,
            "conversationId": conversation_id,
            "intent": intent,
        },
    }


@router.get("/conversations")
async def get_conversations(request: Request):
    """返回当前店铺下可恢复?AI Chat 会话列表?"""
    identity = gateway_identity(request)
    shop_id = requested_shop(request, identity)
    return {"conversations": list_conversations(identity["tenant_id"], shop_id, identity["user_id"])}


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(conversation_id: str, request: Request):
    """返回一个会话的历史消息，供刷新页面后恢复聊天记录?"""
    identity = gateway_identity(request)
    shop_id = requested_shop(request, identity)
    return {"messages": list_messages(identity["tenant_id"], shop_id, identity["user_id"], conversation_id)}


@router.get("/messages/{message_id}")
async def get_ai_chat_message(message_id: str, request: Request):
    """返回单条 AI Chat assistant 消息，前端轮询补?WebSocket 断线时使用?"""
    identity = gateway_identity(request)
    shop_id = requested_shop(request, identity)
    message = get_message(identity["tenant_id"], shop_id, identity["user_id"], message_id)
    if not message:
        raise HTTPException(status_code=404, detail="消息不存在或无权访问")
    return {"message": message}


@router.get("/tasks/{task_id}/timeline")
async def get_ai_chat_task_timeline(task_id: str, request: Request):
    """返回 AI Chat 任务时间线；如果 WebSocket 断开，前端用它补拉真实事件?"""
    identity = gateway_identity(request)
    shop_id = requested_shop(request, identity)
    run = get_run_by_task(identity["tenant_id"], shop_id, identity["user_id"], task_id)
    if not run:
        raise HTTPException(status_code=404, detail="任务不存在或无权访问")
    timeline = build_task_timeline(task_id)
    timeline["run"] = run
    return timeline


@router.post("/tasks/{task_id}/cancel")
async def cancel_ai_chat_task(task_id: str, request: Request):
    """取消当前用户可访问的 AI Chat 后台任务，并把取消状态写?MySQL 消息?"""
    identity = gateway_identity(request)
    shop_id = requested_shop(request, identity)
    run = get_run_by_task(identity["tenant_id"], shop_id, identity["user_id"], task_id)
    if not run:
        raise HTTPException(status_code=404, detail="任务不存在或无权访问")
    if run.get("status") in {"completed", "failed", "timeout", "cancelled"}:
        return {"taskId": task_id, "cancelled": False, "status": run.get("status")}
    cancelled = await task_runtime.cancel(task_id)
    fail_chat_run(
        tenant_id=identity["tenant_id"],
        shop_id=shop_id,
        user_id=identity["user_id"],
        task_id=task_id,
        error_message="用户已取消本?AI Chat 分析?",
        status="cancelled",
    )
    return {"taskId": task_id, "cancelled": cancelled, "status": "cancelled"}


def build_agent_chat_query(user_question: str) -> str:
    """把短问句包装?AgentRuntime 任务，不?API route 中生成业务答案?"""
    return f"""
【AI对话任务?
用户问题：{user_question}

请结合当前店铺经营数据、商品表现、库存、活动和电商运营经验回答?
如果需要，请通过 AgentRuntime 已路由的 workflow 或工具读取当前店铺商品、订单、库存、活动数据?
回答必须直接回应用户问题，不要只罗列通用指标?

商业化约束：
- API route 只负责受理任务，不能拼接规则答案?
- SQL 只能作为 workflow/tool 的数据源，最终回答必须由 AgentRuntime 控制输出?
- 如果没有接入平台行情、搜索热度或外部网络数据，请明确说明，不要假装知道全网行情?
""".strip()


def _runtime_profile_for_chat(payload: dict, task_plan) -> str:
    requested = str(payload.get("runtimeProfile") or payload.get("runtime_profile") or "").strip().lower()
    if requested in {"realtime", "standard", "deep"}:
        return requested
    if task_plan.requires_clarification or task_plan.boundary:
        if task_plan.boundary and task_plan.fallback_reason == "realtime_profile_not_supported":
            return "standard"
        return "realtime"
    if task_plan.assignments:
        return "standard"
    return "realtime"
