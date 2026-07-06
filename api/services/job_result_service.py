"""Agent 任务结果业务落库服务。

DeepAgent/task_runtime 负责执行和内存态任务状态，本服务负责把最终结果写回可持久化的业务表：
agent_jobs、business_reports 和 strategy_reviews。普通 /api/task 没有 agent_jobs 记录时，本服务会直接
跳过，避免影响原有 WebSocket、trace 和通用任务能力。
"""

from __future__ import annotations

import uuid
from typing import Any

from api.db import execute, fetch_all, fetch_one
from api.services.business_text import build_strategy_candidates_from_agent_result, markdown_summary
from api.services.result_payload import result_markdown, structured_json


REPORT_JOB_TYPES = {
    "daily_report": "daily",
    "inventory_risk_scan": "inventory",
    "replenishment_plan": "inventory",
    "campaign_review": "campaign",
    "product_optimization": "product",
    "hot_product_analysis": "product",
    "weekly_report": "weekly",
    "monthly_report": "monthly",
}


async def finalize_agent_job_success(
    tenant_id: str,
    shop_id: str,
    user_id: str,
    task_id: str,
    conversation_id: str,
    final_result: str,
    execution_metadata: dict | None = None,
) -> None:
    """处理 Agent 成功结果，并写回业务表。

    幂等策略：
    - agent_jobs 按 task_id 定位，非业务任务直接跳过；
    - business_reports 优先更新 job.result_report_id 指向的草稿；没有草稿但任务类型需要报告时，
      用 source_task_id=task_id 查找或创建报告；
    - strategy_reviews 先检查 source_task_id=task_id，已有则不重复写入。
    """
    job = _find_job(tenant_id, shop_id, task_id)
    if not job:
        return
    report_content = result_markdown(final_result).strip() or "报告已生成，但内容为空，请重新运行。"
    result_json = structured_json(final_result)
    summary = markdown_summary(report_content)
    execute(
        """
        UPDATE agent_jobs
        SET status='completed', result_summary_json=%s, error_message=NULL, updated_at=NOW()
        WHERE tenant_id=%s AND shop_id=%s AND task_id=%s
        """,
        (result_json, tenant_id, shop_id, task_id),
    )
    _upsert_report_for_job(tenant_id, shop_id, user_id, job, task_id, report_content, summary, result_json)
    _insert_strategy_candidates(tenant_id, shop_id, task_id, build_strategy_candidates_from_agent_result(report_content, job))


async def finalize_agent_job_failure(
    tenant_id: str,
    shop_id: str,
    user_id: str,
    task_id: str,
    conversation_id: str,
    error_message: str,
) -> None:
    """处理 Agent 失败结果，并写回业务 job 和草稿报告。

    失败不抛出二次异常，避免覆盖原始 Agent 错误；报告表没有 failed 枚举约束，当前统一保留 draft，
    并把 summary 改成失败原因，前端仍能解释任务为何没有产出。
    """
    job = _find_job(tenant_id, shop_id, task_id)
    if not job:
        return
    message = (error_message or "数字员工执行失败")[:1000]
    execute(
        """
        UPDATE agent_jobs
        SET status='failed', error_message=%s, updated_at=NOW()
        WHERE tenant_id=%s AND shop_id=%s AND task_id=%s
        """,
        (message, tenant_id, shop_id, task_id),
    )
    report_id = job.get("result_report_id")
    if report_id:
        execute(
            """
            UPDATE agent_jobs SET result_report_id=%s, updated_at=NOW()
            WHERE tenant_id=%s AND shop_id=%s AND id=%s
            """,
            (report_id, tenant_id, shop_id, job["id"]),
        )
        execute(
            """
            UPDATE business_reports
            SET status='draft', summary=%s, content_markdown=%s, source_task_id=%s, updated_at=NOW()
            WHERE tenant_id=%s AND shop_id=%s AND id=%s
            """,
            (f"报告生成失败：{message[:180]}", f"报告生成失败：{message}", task_id, tenant_id, shop_id, report_id),
        )
    _ = user_id, conversation_id


def _find_job(tenant_id: str, shop_id: str, task_id: str) -> dict[str, Any] | None:
    """按可信租户/店铺/task_id 定位业务 job。"""
    return fetch_one(
        """
        SELECT id, tenant_id, shop_id, agent_id, agent_name, job_type, title, status, task_id,
               conversation_id, result_report_id, params_json, created_by
        FROM agent_jobs
        WHERE tenant_id=%s AND shop_id=%s AND task_id=%s
        LIMIT 1
        """,
        (tenant_id, shop_id, task_id),
    )


def _upsert_report_for_job(tenant_id: str, shop_id: str, user_id: str, job: dict[str, Any], task_id: str, final_result: str, summary: str, structured_result_json: str | None) -> str | None:
    """更新或创建任务产出的经营报告。"""
    report_id = job.get("result_report_id")
    if report_id:
        execute(
            """
            UPDATE business_reports
            SET content_markdown=%s, structured_json=%s, summary=%s, status='ready', source_task_id=%s, updated_at=NOW()
            WHERE tenant_id=%s AND shop_id=%s AND id=%s
            """,
            (final_result, structured_result_json, summary, task_id, tenant_id, shop_id, report_id),
        )
        return str(report_id)

    report_type = REPORT_JOB_TYPES.get(str(job.get("job_type") or ""))
    if not report_type:
        return None
    existing = fetch_one(
        """
        SELECT id FROM business_reports
        WHERE tenant_id=%s AND shop_id=%s AND source_task_id=%s
        LIMIT 1
        """,
        (tenant_id, shop_id, task_id),
    )
    if existing:
        report_id = existing["id"]
        execute(
            """
            UPDATE business_reports
            SET content_markdown=%s, structured_json=%s, summary=%s, status='ready', updated_at=NOW()
            WHERE tenant_id=%s AND shop_id=%s AND id=%s
            """,
            (final_result, structured_result_json, summary, tenant_id, shop_id, report_id),
        )
        return str(report_id)

    report_id = str(uuid.uuid4())
    execute(
        """
        INSERT INTO business_reports (id, tenant_id, shop_id, type, title, summary, content_markdown, structured_json, status, source_task_id, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'ready', %s, %s)
        """,
        (report_id, tenant_id, shop_id, report_type, job.get("title") or "数字员工报告", summary, final_result, structured_result_json, task_id, user_id),
    )
    execute(
        """
        UPDATE agent_jobs SET result_report_id=%s, updated_at=NOW()
        WHERE tenant_id=%s AND shop_id=%s AND id=%s
        """,
        (report_id, tenant_id, shop_id, job["id"]),
    )
    return report_id


def _insert_strategy_candidates(tenant_id: str, shop_id: str, task_id: str, candidates: list[dict[str, str]]) -> int:
    """写入 Agent 策略候选，同一个 task_id 只写一次。"""
    existing = fetch_all(
        "SELECT id FROM strategy_reviews WHERE tenant_id=%s AND shop_id=%s AND source_task_id=%s LIMIT 1",
        (tenant_id, shop_id, task_id),
    )
    if existing:
        return 0
    inserted = 0
    for candidate in candidates[:3]:
        title = (candidate.get("title") or "").strip()
        if not title:
            continue
        risk_level = candidate.get("risk_level") if candidate.get("risk_level") in {"low", "medium", "high"} else "medium"
        execute(
            """
            INSERT INTO strategy_reviews (id, tenant_id, shop_id, title, source, expected_impact, risk_level, status, source_task_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s)
            """,
            (
                str(uuid.uuid4()),
                tenant_id,
                shop_id,
                title[:255],
                (candidate.get("source") or "数字员工")[:128],
                candidate.get("expected_impact") or "预计提升经营执行效率",
                risk_level,
                task_id,
            ),
        )
        inserted += 1
    return inserted
