"""经营报告服务。"""

from __future__ import annotations

import uuid

from api.db import execute, fetch_one
from api.services.agent_job_service import create_agent_job
from api.services.ecommerce_queries import list_reports
from api.services.result_payload import parse_structured_json


async def generate_report(tenant_id: str, shop_id: str, user_id: str, payload: dict) -> dict:
    """创建报告草稿并启动对应数字员工任务。"""
    report_type = payload.get("type") or "daily"
    title = payload.get("title") or "经营报告"
    report_id = str(uuid.uuid4())
    draft_content = "报告生成中，数字员工正在处理数据。"
    execute(
        """
        INSERT INTO business_reports (id, tenant_id, shop_id, type, title, summary, content_markdown, status, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'draft', %s)
        """,
        (report_id, tenant_id, shop_id, report_type, title, draft_content, draft_content, user_id),
    )
    job = await create_agent_job(tenant_id, shop_id, user_id, payload.get("agentId") or "store-analyst", {"jobType": _report_job_type(report_type), "title": title, "params": {"reportId": report_id}})
    execute("UPDATE business_reports SET source_task_id=%s WHERE tenant_id=%s AND shop_id=%s AND id=%s", (job["taskId"], tenant_id, shop_id, report_id))
    return {"reportId": report_id, "job": job}


def get_report(tenant_id: str, shop_id: str, report_id: str) -> dict | None:
    row = fetch_one("SELECT id, type, title, summary, content_markdown, structured_json, status, created_at FROM business_reports WHERE tenant_id=%s AND shop_id=%s AND id=%s", (tenant_id, shop_id, report_id))
    if not row:
        return None
    return {"id": row["id"], "type": row["type"], "title": row["title"], "summary": row["summary"], "contentMarkdown": row.get("content_markdown") or row.get("summary") or "报告内容生成中。", "structuredResult": parse_structured_json(row.get("structured_json")), "status": row["status"], "createdAt": str(row["created_at"])}


def _report_job_type(report_type: str) -> str:
    return {"daily": "daily_report", "weekly": "weekly_report", "monthly": "monthly_report", "inventory": "inventory_risk_scan", "campaign": "campaign_review", "product": "product_optimization"}.get(report_type, "daily_report")
