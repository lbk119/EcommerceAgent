"""数字员工任务服务?"""

from __future__ import annotations

import uuid
import json

from api.db import ensure_platform_schema, execute, fetch_all, fetch_one
from api.services.job_result_service import finalize_agent_job_failure, finalize_agent_job_success
from api.services.result_payload import parse_structured_json
from api.services.agent_job_prompts import build_agent_query
from api.services.ecommerce_queries import AGENT_DEFINITIONS, normalize_time
from api.task_queue import task_queue
from api.task_runtime import task_runtime
from agent.plan.planner import planner_agent


def agent_name(agent_id: str) -> str:
    return next((agent["name"] for agent in AGENT_DEFINITIONS if agent["id"] == agent_id), agent_id)


async def create_agent_job(tenant_id: str, shop_id: str, user_id: str, agent_id: str, payload: dict) -> dict:
    """创建业务 job，并复用现有 Agent task_queue/task_runtime 执行?"""
    ensure_platform_schema()
    job_type = payload.get("jobType") or payload.get("job_type")
    title = payload.get("title") or job_type or "数字员工任务"
    params = payload.get("params") or {}
    try:
        query = build_agent_query(agent_id, job_type, params)
    except ValueError as error:
        raise ValueError(f"无法创建数字员工任务：{error}") from error
    conversation_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    result_report_id = params.get("reportId") or params.get("report_id")
    runtime_profile = str(payload.get("runtimeProfile") or payload.get("runtime_profile") or _runtime_profile_for_job(job_type))
    task_plan = _task_plan_for_job(job_type, query, runtime_profile)
    execute(
        """
        INSERT INTO agent_jobs (id, tenant_id, shop_id, agent_id, agent_name, job_type, title, status, task_id, conversation_id, params_json, result_report_id, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'running', %s, %s, %s, %s, %s)
        """,
        (job_id, tenant_id, shop_id, agent_id, agent_name(agent_id), job_type, title, task_id, conversation_id, json.dumps(params, ensure_ascii=False), result_report_id, user_id),
    )
    await task_runtime.enqueue(task_id, query, metadata={"conversation_id": conversation_id, "tenant_id": tenant_id, "shop_id": shop_id, "user_id": user_id, "agent_job_id": job_id, "runtime_profile": runtime_profile, "task_plan": task_plan.to_lightweight_dict(), "intent": task_plan.primary_task_type})
    await task_queue.enqueue({"query": query, "conversation_id": conversation_id, "thread_id": conversation_id, "task_id": task_id, "tenant_id": tenant_id, "shop_id": shop_id, "user_id": user_id, "runtime_profile": runtime_profile, "job_type": job_type, "source": "agent_job", "intent": task_plan.primary_task_type, "task_plan": task_plan.to_dict()})
    return {"id": job_id, "jobId": job_id, "agentId": agent_id, "jobType": job_type, "title": title, "status": "running", "taskId": task_id, "conversationId": conversation_id, "resultReportId": result_report_id, "runtimeProfile": runtime_profile}


def _runtime_profile_for_job(job_type: str | None) -> str:
    """数字员工默认?standard；管理层报告和复杂周期报告才?deep?"""
    deep_job_types = {"weekly_report", "monthly_report", "management_report", "strategy_diagnosis", "cross_platform_attribution"}
    return "deep" if str(job_type or "") in deep_job_types else "standard"


def _task_plan_for_job(job_type: str | None, query: str, runtime_profile: str):
    """用稳?jobType 生成计划，避免内部长 prompt 误命中过?capability?"""
    planner_query = {
        "product_optimization": "商品优化",
        "hot_product_analysis": "爆品分析",
        "daily_report": "经营日报",
        "weekly_report": "经营周报",
        "campaign_review": "活动复盘",
        "inventory_risk_scan": "库存风险",
        "replenishment_plan": "补货计划",
    }.get(str(job_type or ""), query)
    plan = planner_agent.plan(planner_query, profile=runtime_profile)
    plan_data = plan.to_dict()
    plan_data["raw_query"] = query
    plan_data["metadata"] = {**plan.metadata, "normalized_query": query}
    from agent.plan.models import AgentTaskPlan

    return AgentTaskPlan.from_dict(plan_data)


def list_agent_jobs(tenant_id: str, shop_id: str, agent_id: str | None = None) -> list[dict]:
    params = [tenant_id, shop_id]
    sql = "SELECT id, agent_id, agent_name, job_type, title, status, task_id, conversation_id, result_report_id, error_message, created_at FROM agent_jobs WHERE tenant_id=%s AND shop_id=%s"
    if agent_id:
        sql += " AND agent_id=%s"
        params.append(agent_id)
    sql += " ORDER BY created_at DESC LIMIT 100"
    rows = fetch_all(sql, params)
    return [{"id": row["id"], "jobId": row["id"], "agentId": row["agent_id"], "agentName": row["agent_name"], "jobType": row["job_type"], "title": row["title"], "status": row["status"], "taskId": row.get("task_id"), "conversationId": row.get("conversation_id"), "resultReportId": row.get("result_report_id"), "errorMessage": row.get("error_message"), "createdAt": normalize_time(row["created_at"])} for row in rows]


async def sync_agent_job_from_runtime(tenant_id: str, shop_id: str, user_id: str, job_id: str) -> None:
    """Lightly sync a running business job from in-memory runtime state."""
    row = fetch_one("SELECT task_id, conversation_id, status FROM agent_jobs WHERE tenant_id=%s AND shop_id=%s AND id=%s", (tenant_id, shop_id, job_id))
    if not row or row.get("status") != "running" or not row.get("task_id"):
        return
    state = await task_runtime.get_scoped(row["task_id"], {"tenant_id": tenant_id, "shop_id": shop_id, "user_id": user_id})
    if not state:
        return
    if state.get("status") == "failed":
        await finalize_agent_job_failure(
            tenant_id=tenant_id,
            shop_id=shop_id,
            user_id=user_id,
            task_id=row["task_id"],
            conversation_id=row.get("conversation_id") or "",
            error_message=state.get("error") or "任务执行失败",
        )
    elif state.get("status") == "succeeded" and state.get("result") is not None:
        await finalize_agent_job_success(
            tenant_id=tenant_id,
            shop_id=shop_id,
            user_id=user_id,
            task_id=row["task_id"],
            conversation_id=row.get("conversation_id") or "",
            final_result=_runtime_result_from_state(state),
            execution_metadata={"syncedFromRuntime": True},
        )


def get_agent_job(tenant_id: str, shop_id: str, job_id: str) -> dict | None:
    row = fetch_one("SELECT id, agent_id, agent_name, job_type, title, status, task_id, conversation_id, result_report_id, result_summary_json, error_message, created_at FROM agent_jobs WHERE tenant_id=%s AND shop_id=%s AND id=%s", (tenant_id, shop_id, job_id))
    if not row:
        return None
    return {"id": row["id"], "jobId": row["id"], "agentId": row["agent_id"], "agentName": row["agent_name"], "jobType": row["job_type"], "title": row["title"], "status": row["status"], "taskId": row.get("task_id"), "conversationId": row.get("conversation_id"), "resultReportId": row.get("result_report_id"), "structuredResult": parse_structured_json(row.get("result_summary_json")), "errorMessage": row.get("error_message"), "createdAt": normalize_time(row["created_at"])}


def _runtime_result_from_state(state: dict):
    from agent.runtime.execution_result import FinalResult

    return FinalResult(content=str(state.get("result") or ""), structured_result=state.get("structured_result") or {})
