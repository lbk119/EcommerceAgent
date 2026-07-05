"""数据导入后的确定性业务刷新服务。

导入完成后不自动触发多个大模型任务，而是基于已经入库的经营表重新聚合指标，生成一份稳定的经营
概览报告和少量待审核策略。这样前端回到工作台时能立即看到真实变化，也不会因为导入一次就产生
大量模型成本。
"""

from __future__ import annotations

import uuid
from typing import Any

from api.db import execute, fetch_one
from api.services.business_text import build_import_overview_markdown, build_strategy_candidates_from_metrics, markdown_summary
from api.services.ecommerce_queries import get_metrics, list_campaigns, list_inventory_risks, list_products


def run_post_import_refresh(tenant_id: str, shop_id: str, user_id: str, import_job_id: str) -> dict[str, Any]:
    """导入完成后的业务刷新入口。

    返回值会透传给前端：workspaceShouldRefresh 提醒前端刷新工作台；generatedReportId 和
    generatedStrategiesCount 方便导入页展示本次导入产生了哪些业务资产。
    """
    execute(
        """
        UPDATE gateway_shops
        SET data_status='imported', last_sync_at=NOW(), updated_at=NOW()
        WHERE tenant_id=%s AND id=%s
        """,
        (tenant_id, shop_id),
    )
    metrics = get_metrics(tenant_id, shop_id)
    products = list_products(tenant_id, shop_id)
    inventory_risks = list_inventory_risks(tenant_id, shop_id)
    campaigns = list_campaigns(tenant_id, shop_id)
    content = build_import_overview_markdown(metrics, products, inventory_risks, campaigns)
    report_id = _upsert_import_report(tenant_id, shop_id, user_id, import_job_id, content)
    strategies_count = _insert_import_strategies(
        tenant_id,
        shop_id,
        import_job_id,
        build_strategy_candidates_from_metrics(metrics, products, inventory_risks, campaigns),
    )
    return {
        "workspaceShouldRefresh": True,
        "generatedReportId": report_id,
        "generatedStrategiesCount": strategies_count,
    }


def _upsert_import_report(tenant_id: str, shop_id: str, user_id: str, import_job_id: str, content: str) -> str:
    """按 import_job_id 幂等创建或更新导入概览报告。

    business_reports.source_task_id 复用来保存导入 job id；这里不是 Agent task_id，而是为了让同一次
    数据导入反复确认时不会重复生成多份概览报告。
    """
    existing = fetch_one(
        """
        SELECT id FROM business_reports
        WHERE tenant_id=%s AND shop_id=%s AND source_task_id=%s
        LIMIT 1
        """,
        (tenant_id, shop_id, import_job_id),
    )
    summary = markdown_summary(content)
    if existing:
        execute(
            """
            UPDATE business_reports
            SET title='导入后经营概览', type='daily', summary=%s, content_markdown=%s,
                status='ready', updated_at=NOW()
            WHERE tenant_id=%s AND shop_id=%s AND id=%s
            """,
            (summary, content, tenant_id, shop_id, existing["id"]),
        )
        return str(existing["id"])
    report_id = str(uuid.uuid4())
    execute(
        """
        INSERT INTO business_reports (id, tenant_id, shop_id, type, title, summary, content_markdown, status, source_task_id, created_by)
        VALUES (%s, %s, %s, 'daily', '导入后经营概览', %s, %s, 'ready', %s, %s)
        """,
        (report_id, tenant_id, shop_id, summary, content, import_job_id, user_id),
    )
    return report_id


def _insert_import_strategies(tenant_id: str, shop_id: str, import_job_id: str, candidates: list[dict[str, str]]) -> int:
    """按 import_job_id 幂等写入导入巡检策略。"""
    existing = fetch_one(
        "SELECT id FROM strategy_reviews WHERE tenant_id=%s AND shop_id=%s AND source_task_id=%s LIMIT 1",
        (tenant_id, shop_id, import_job_id),
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
                candidate.get("source") or "数据导入巡检",
                candidate.get("expected_impact") or "预计提升经营分析效率",
                risk_level,
                import_job_id,
            ),
        )
        inserted += 1
    return inserted
