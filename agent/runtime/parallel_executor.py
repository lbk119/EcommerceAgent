"""固定 DAG 并行执行器。

ParallelExecutor 只执行 PlanRegistry 生成的计划，不做自由规划。每个 step 独立 timeout，整体任务也有
global timeout；非关键 step 失败会以结构化缺失数据返回，关键 step 失败则交由上层决定 fallback
DeepAgent 或返回降级结论。
"""

from __future__ import annotations

import asyncio
import csv
import io
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

from agent.observability.tracer import tracer
from agent.runtime.plan_registry import ExecutionPlan, PlanStep
from agent.runtime.task_profiles import TaskExecutionProfile


@dataclass(frozen=True)
class StepResult:
    """单个计划 step 的结构化结果。

    status 使用 ok/failed/timeout；critical 表示该 step 失败是否会导致整体结果降级或 fallback。
    rows 保存 JSON-ready 结构化数据，summary 用于 trace/前端快速展示。
    """

    step: str
    label: str
    status: str
    critical: bool
    step_id: str = ""
    expert: str = "data_expert"
    dependencyStatus: str = "ready"
    rows: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    latencyMs: float = 0
    error: str = ""
    missingData: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "step_id": self.step_id or self.step,
            "label": self.label,
            "status": self.status,
            "critical": self.critical,
            "expert": self.expert,
            "dependencyStatus": self.dependencyStatus,
            "rows": self.rows,
            "summary": self.summary,
            "latencyMs": self.latencyMs,
            "error": self.error,
            "missingData": self.missingData,
        }


@dataclass(frozen=True)
class PlanRunResult:
    """一次计划并行执行的整体结果。"""

    plan: ExecutionPlan
    step_results: list[StepResult]
    latencyMs: float
    timedOut: bool = False

    @property
    def has_critical_failure(self) -> bool:
        return any(result.critical and result.status != "ok" for result in self.step_results)

    def to_sections(self) -> dict[str, str]:
        """转成 ExecutionResult.sections 可存储的 JSON 字符串。"""
        import json

        return {result.step: json.dumps(result.to_dict(), ensure_ascii=False) for result in self.step_results}


class ParallelExecutor:
    """执行固定 DAG 的并行 runner。"""

    def __init__(self, profile: TaskExecutionProfile):
        self.profile = profile

    async def execute(self, plan: ExecutionPlan, *, trace_id: str, task_id: str, conversation_id: str) -> PlanRunResult:
        """执行计划 DAG。

        无依赖 step 会在同一批并行执行；有 depends_on 的 step 等依赖成功后再进入下一批。
        """
        started_at = time.perf_counter()
        plan_id = str(plan.metadata.get("task_plan", {}).get("plan_id") or plan.metadata.get("plan_id") or "")
        tracer.emit(
            "plan_execution_started",
            trace_id=trace_id,
            task_id=task_id,
            conversation_id=conversation_id,
            agent_name="parallel_executor",
            metadata={"workflow_name": plan.workflow_name, "plan_id": plan_id, "step_count": len(plan.steps), "profile": self.profile.name, "global_timeout_seconds": self.profile.global_timeout_seconds},
        )
        step_results: list[StepResult] = []
        completed: dict[str, StepResult] = {}
        remaining = list(plan.steps)
        timed_out = False
        while remaining:
            elapsed = time.perf_counter() - started_at
            remaining_timeout = self.profile.global_timeout_seconds - elapsed
            if remaining_timeout <= 0:
                timed_out = True
                for step in remaining:
                    step_results.append(self._timeout_result(step, self.profile.global_timeout_seconds, "plan global timeout", dependency_status="global_timeout"))
                break
            ready, blocked = _ready_steps(remaining, completed)
            if not ready:
                for step in blocked:
                    step_results.append(StepResult(step=step.name, step_id=step.step_id or step.name, label=step.label, status="failed", critical=step.critical, expert=step.expert, dependencyStatus="blocked", error="dependency not satisfied", missingData=[step.label]))
                break
            for step in ready:
                metadata = self._step_metadata(plan, step, dependency_status="ready")
                tracer.emit("plan_step_started", trace_id=trace_id, task_id=task_id, conversation_id=conversation_id, agent_name="parallel_executor", metadata=metadata)
                tracer.emit("workflow_step_started", trace_id=trace_id, task_id=task_id, conversation_id=conversation_id, agent_name="parallel_executor", metadata=metadata)
            tasks = [asyncio.create_task(self._run_step(plan, step, trace_id=trace_id, task_id=task_id, conversation_id=conversation_id)) for step in ready]
            done, pending = await asyncio.wait(tasks, timeout=remaining_timeout)
            if pending:
                timed_out = True
            for task in done:
                result = task.result()
                step_results.append(result)
                completed[result.step_id or result.step] = result
                completed[result.step] = result
            for task in pending:
                task.cancel()
            for task, step in zip(tasks, ready):
                if task in pending:
                    result = self._timeout_result(step, remaining_timeout, "plan global timeout", dependency_status="global_timeout")
                    step_results.append(result)
                    completed[result.step_id or result.step] = result
                    completed[result.step] = result
            remaining = [step for step in blocked if step not in ready]
        order = {step.step_id or step.name: index for index, step in enumerate(plan.steps)}
        step_results.sort(key=lambda result: order.get(result.step_id or result.step, 999))
        latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
        tracer.emit(
            "plan_execution_finished",
            trace_id=trace_id,
            task_id=task_id,
            conversation_id=conversation_id,
            agent_name="parallel_executor",
            latency_ms=latency_ms,
            metadata={"workflow_name": plan.workflow_name, "plan_id": plan_id, "profile": self.profile.name, "timed_out": timed_out, "critical_failed": any(result.critical and result.status != "ok" for result in step_results), "steps": [result.to_dict() for result in step_results]},
        )
        return PlanRunResult(plan=plan, step_results=step_results, latencyMs=latency_ms, timedOut=timed_out)

    async def _run_step(self, plan: ExecutionPlan, step: PlanStep, *, trace_id: str, task_id: str, conversation_id: str) -> StepResult:
        """执行单个 step，并把 CSV/文本查询结果转换成结构化 JSON。"""
        timeout_seconds = float(step.timeout_seconds or self.profile.step_timeout_seconds)
        metadata = self._step_metadata(plan, step)
        started_at = time.perf_counter()
        try:
            worker_task = asyncio.create_task(asyncio.to_thread(self._invoke_step, step, plan))
            done, pending = await asyncio.wait({worker_task}, timeout=timeout_seconds)
            if pending:
                # 单 step 超时不等待底层线程返回，直接把该 step 标记为 timeout。
                worker_task.cancel()
                raise asyncio.TimeoutError()
            raw_result = next(iter(done)).result()
            if isinstance(raw_result, list):
                rows = raw_result
                raw_text = ""
            else:
                raw_text = str(raw_result or "")
                rows = _parse_rows(raw_text)
            rows = _post_process_rows(step, rows)
            if _is_query_error(raw_text):
                raise RuntimeError(raw_text[:500])
            if not rows:
                raise RuntimeError("empty_result")
            latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
            result = StepResult(step=step.name, step_id=step.step_id or step.name, label=step.label, status="ok", critical=step.critical, expert=step.expert, dependencyStatus="ready", rows=rows, summary=_summarize_step(step.name, rows), latencyMs=latency_ms)
            finish_metadata = {**metadata, "row_count": len(rows), "summary": result.summary, "structured": True, "result_preview": result.summary}
            tracer.emit("plan_step_finished", trace_id=trace_id, task_id=task_id, conversation_id=conversation_id, agent_name="parallel_executor", latency_ms=latency_ms, metadata=finish_metadata)
            tracer.emit("workflow_step_finished", trace_id=trace_id, task_id=task_id, conversation_id=conversation_id, agent_name="parallel_executor", latency_ms=latency_ms, metadata=finish_metadata)
            return result
        except Exception as error:
            latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
            status = "timeout" if isinstance(error, asyncio.TimeoutError) else "failed"
            result = StepResult(step=step.name, step_id=step.step_id or step.name, label=step.label, status=status, critical=step.critical, expert=step.expert, dependencyStatus="ready", latencyMs=latency_ms, error=str(error)[:1000], missingData=[step.label])
            fail_metadata = {**metadata, "status": status, "missing_data": result.missingData, "structured": True}
            tracer.emit("plan_step_failed", trace_id=trace_id, task_id=task_id, conversation_id=conversation_id, agent_name="parallel_executor", latency_ms=latency_ms, error=result.error, metadata=fail_metadata)
            tracer.emit("workflow_step_failed", trace_id=trace_id, task_id=task_id, conversation_id=conversation_id, agent_name="parallel_executor", latency_ms=latency_ms, error=result.error, metadata=fail_metadata)
            return result

    def _step_metadata(self, plan: ExecutionPlan, step: PlanStep, *, dependency_status: str = "ready") -> dict[str, Any]:
        timeout_seconds = float(step.timeout_seconds or self.profile.step_timeout_seconds)
        plan_id = str(plan.metadata.get("task_plan", {}).get("plan_id") or plan.metadata.get("plan_id") or "")
        return {"plan_id": plan_id, "step_id": step.step_id or step.name, "expert": step.expert, "dependency_status": dependency_status, "depends_on": list(step.depends_on), "step_name": step.label, "step_key": step.name, "required": step.critical, "critical": step.critical, "time_range": plan.time_range.label, "workflow_name": plan.workflow_name, "parallel_group": plan.workflow_name, "timeout_seconds": timeout_seconds}

    def _timeout_result(self, step: PlanStep, timeout_seconds: float, error: str, *, dependency_status: str) -> StepResult:
        return StepResult(step=step.name, step_id=step.step_id or step.name, label=step.label, status="timeout", critical=step.critical, expert=step.expert, dependencyStatus=dependency_status, latencyMs=round(timeout_seconds * 1000, 2), error=error, missingData=[step.label])

    def _invoke_step(self, step: PlanStep, plan: ExecutionPlan) -> str | list[dict[str, Any]]:
        """调用固定业务查询函数。"""
        from agent.workflows import business_metrics as metrics

        limit = int(step.params.get("limit") or 8)
        structured = self._invoke_structured_step(step, plan, limit=limit)
        if structured is not None:
            return structured
        registry: dict[str, Callable[[], str]] = {
            "query_daily_metrics": lambda: metrics.query_daily_metrics(plan.time_range),
            "query_daily_risks": lambda: metrics.query_daily_risks(plan.time_range),
            "query_shop_profile": metrics.query_shop_profile,
            "query_inventory_risks": lambda: metrics.query_inventory_risks(plan.time_range),
            "query_inventory_velocity": lambda: metrics.query_inventory_velocity(plan.time_range),
            "query_sales_trend": lambda: metrics.query_daily_metrics(plan.time_range),
            "query_campaign_traffic": lambda: metrics.query_campaign_traffic(plan.time_range),
            "query_campaign_roi": lambda: metrics.query_campaign_roi(plan.time_range),
            "query_campaign_risks": lambda: metrics.query_campaign_risks(plan.time_range),
            "query_hot_products": lambda: metrics.query_hot_products(plan.time_range, limit=limit),
            "query_low_conversion_products": lambda: metrics.query_hot_products(plan.time_range, limit=max(limit, 12)),
        }
        if step.name not in registry:
            raise ValueError(f"未知计划 step: {step.name}")
        return registry[step.name]()

    def _invoke_structured_step(self, step: PlanStep, plan: ExecutionPlan, *, limit: int) -> list[dict[str, Any]] | None:
        """高频计划节点直接返回结构化 rows。

        旧 workflow 函数为了兼容 LangChain tool，会把 SQL 结果序列化成 CSV 文本，再由执行器解析回来。
        商业化的固定 DAG 不需要这层文本往返：这里对商品、库存、活动等高频节点直接使用字典游标返回
        JSON-ready rows，减少 CSV 编解码、长文本 trace 和重复全量聚合带来的延迟抖动。
        """
        if step.name == "query_hot_products":
            return _query_hot_product_rows(plan, limit=min(max(limit, 1), 20))
        if step.name == "query_low_conversion_products":
            return _query_low_conversion_rows(plan, limit=min(max(limit, 1), 20))
        if step.name == "query_inventory_velocity":
            return _query_inventory_velocity_rows(plan, limit=20)
        if step.name == "query_campaign_roi":
            return _query_campaign_roi_rows(limit=20)
        return None


def _execute_rows(query: str) -> list[dict[str, Any]]:
    """执行固定 SQL 并返回可 JSON 序列化的 dict rows。"""
    from mysql.connector import connect

    from agent.core.db import get_db_config

    with connect(**get_db_config()) as conn:
        with conn.cursor(dictionary=True) as cursor:
            cursor.execute(query)
            return [{key: _json_value(value) for key, value in row.items()} for row in cursor.fetchall()]


def _ready_steps(steps: list[PlanStep], completed: dict[str, StepResult]) -> tuple[list[PlanStep], list[PlanStep]]:
    """根据依赖完成状态拆出当前可执行 step。"""
    ready: list[PlanStep] = []
    blocked: list[PlanStep] = []
    pending_keys = {step.step_id or step.name for step in steps} | {step.name for step in steps}
    for step in steps:
        dependencies = list(step.depends_on or [])
        if not dependencies:
            ready.append(step)
            continue
        failed_dependency = any((completed.get(dep) and completed[dep].status != "ok") for dep in dependencies)
        if failed_dependency:
            blocked.append(step)
            continue
        waiting_dependency = any(dep not in completed and dep in pending_keys for dep in dependencies)
        if waiting_dependency:
            blocked.append(step)
        else:
            ready.append(step)
    return ready, blocked


def _query_hot_product_rows(plan: ExecutionPlan, *, limit: int) -> list[dict[str, Any]]:
    """轻量爆品候选：销售额 + 流量转化 + 库存 + 退款，避免旧版大 CTE 在并发下互相拖慢。"""
    from agent.workflows import business_metrics as metrics

    order_scope = metrics._required_data_scope("o")
    item_scope = metrics._required_data_scope("oi")
    product_scope = metrics._required_data_scope("p")
    inventory_scope = metrics._required_data_scope("i")
    traffic_scope = metrics._required_data_scope("ts")
    refund_scope = metrics._required_data_scope("rf")
    order_bound_scope = metrics._required_data_scope("orders")
    traffic_bound_scope = metrics._required_data_scope("traffic_stats")
    refund_bound_scope = metrics._required_data_scope("refunds")
    order_filter = metrics._time_filter("o.order_purchase_timestamp", "ob.max_time", plan.time_range)
    traffic_filter = metrics._time_filter("ts.stat_date", "tb.max_time", plan.time_range)
    refund_filter = metrics._time_filter("rf.refund_time", "rb.max_time", plan.time_range)
    return _execute_rows(f"""
WITH order_bounds AS (
    SELECT MAX(order_purchase_timestamp) AS max_time FROM orders WHERE {order_bound_scope}
), traffic_bounds AS (
    SELECT MAX(stat_date) AS max_time FROM traffic_stats WHERE {traffic_bound_scope}
), refund_bounds AS (
    SELECT MAX(refund_time) AS max_time FROM refunds WHERE {refund_bound_scope}
), sales AS (
    SELECT oi.product_id, COUNT(DISTINCT o.order_id) AS orders_count, COUNT(*) AS units_sold,
           ROUND(SUM(oi.price), 2) AS sales_amount, ROUND(AVG(oi.price), 2) AS avg_price,
           MIN(o.order_purchase_timestamp) AS first_order_time, MAX(o.order_purchase_timestamp) AS last_order_time
    FROM order_items oi
    JOIN orders o ON o.order_id = oi.order_id
    CROSS JOIN order_bounds ob
    WHERE o.order_status IN ('delivered', 'shipped', 'invoiced', 'approved')
      AND {order_scope} AND {item_scope} AND {order_filter}
    GROUP BY oi.product_id
), traffic AS (
    SELECT ts.product_id, SUM(ts.views) AS views, SUM(ts.visitors) AS visitors,
           SUM(ts.conversions) AS conversions,
           ROUND(SUM(ts.conversions) / NULLIF(SUM(ts.visitors), 0), 4) AS conversion_rate
    FROM traffic_stats ts
    CROSS JOIN traffic_bounds tb
    WHERE {traffic_scope} AND {traffic_filter}
    GROUP BY ts.product_id
), refund AS (
    SELECT rf.product_id, COUNT(*) AS refunds_count, ROUND(SUM(rf.refund_amount), 2) AS refund_amount
    FROM refunds rf
    CROSS JOIN refund_bounds rb
    WHERE {refund_scope} AND {refund_filter}
    GROUP BY rf.product_id
)
SELECT s.product_id, p.category_name_en, s.orders_count, s.units_sold, s.sales_amount, s.avg_price,
       COALESCE(t.views, 0) AS views, COALESCE(t.visitors, 0) AS visitors,
       COALESCE(t.conversions, 0) AS conversions, COALESCE(t.conversion_rate, 0) AS conversion_rate,
       COALESCE(i.stock, 0) AS stock, COALESCE(i.safety_stock, 0) AS safety_stock,
       0 AS campaign_impressions, 0 AS campaign_clicks, 0 AS campaign_orders,
       0 AS campaign_revenue, 0 AS campaign_spend, 0 AS campaign_roi,
       COALESCE(r.refunds_count, 0) AS refunds_count, COALESCE(r.refund_amount, 0) AS refund_amount,
       s.first_order_time, s.last_order_time
FROM sales s
JOIN products p ON p.product_id = s.product_id AND {product_scope}
LEFT JOIN traffic t ON t.product_id = s.product_id
LEFT JOIN inventory i ON i.product_id = s.product_id AND {inventory_scope}
LEFT JOIN refund r ON r.product_id = s.product_id
ORDER BY s.sales_amount DESC, s.units_sold DESC
LIMIT {limit}
""")


def _query_low_conversion_rows(plan: ExecutionPlan, *, limit: int) -> list[dict[str, Any]]:
    """低转化候选只读流量和库存，不重复执行完整爆品聚合。"""
    from agent.workflows import business_metrics as metrics

    traffic_scope = metrics._required_data_scope("ts")
    product_scope = metrics._required_data_scope("p")
    inventory_scope = metrics._required_data_scope("i")
    traffic_bound_scope = metrics._required_data_scope("traffic_stats")
    traffic_filter = metrics._time_filter("ts.stat_date", "tb.max_time", plan.time_range)
    return _execute_rows(f"""
WITH traffic_bounds AS (
    SELECT MAX(stat_date) AS max_time FROM traffic_stats WHERE {traffic_bound_scope}
), traffic AS (
    SELECT ts.product_id, SUM(ts.views) AS views, SUM(ts.visitors) AS visitors,
           SUM(ts.conversions) AS conversions,
           ROUND(SUM(ts.conversions) / NULLIF(SUM(ts.visitors), 0), 4) AS conversion_rate
    FROM traffic_stats ts
    CROSS JOIN traffic_bounds tb
    WHERE {traffic_scope} AND {traffic_filter}
    GROUP BY ts.product_id
)
SELECT t.product_id, p.category_name_en, 0 AS orders_count, 0 AS units_sold, 0 AS sales_amount,
       t.views, t.visitors, t.conversions, COALESCE(t.conversion_rate, 0) AS conversion_rate,
       COALESCE(i.stock, 0) AS stock, COALESCE(i.safety_stock, 0) AS safety_stock,
       0 AS refunds_count, 0 AS refund_amount
FROM traffic t
JOIN products p ON p.product_id = t.product_id AND {product_scope}
LEFT JOIN inventory i ON i.product_id = t.product_id AND {inventory_scope}
WHERE t.visitors > 0
ORDER BY conversion_rate ASC, visitors DESC
LIMIT {limit}
""")


def _query_inventory_velocity_rows(plan: ExecutionPlan, *, limit: int) -> list[dict[str, Any]]:
    """库存承接能力直接返回补货字段，避免走 CSV tool。

    商品优化和爆品判断只需要库存是否能承接放量；销量速度已经由商品表现节点覆盖。这里不再重复扫描
    orders/order_items，避免多个并行 step 在同一时间争抢订单聚合资源。
    """
    from agent.workflows import business_metrics as metrics

    inventory_scope = metrics._required_data_scope("i")
    product_scope = metrics._required_data_scope("p")
    return _execute_rows(f"""
SELECT i.product_id, p.category_name_en, i.stock, i.safety_stock,
       0 AS units_sold_30d,
       0 AS avg_daily_units,
       0 AS sales_amount_30d,
       GREATEST(i.safety_stock * 2 - i.stock, 0) AS suggested_replenishment,
       CASE
           WHEN i.stock <= i.safety_stock THEN 'protect_stock'
           ELSE 'normal'
       END AS suggested_action
FROM inventory i
JOIN products p ON p.product_id = i.product_id AND {product_scope}
WHERE {inventory_scope}
ORDER BY suggested_replenishment DESC, i.stock ASC
LIMIT {limit}
""")


def _query_campaign_roi_rows(*, limit: int) -> list[dict[str, Any]]:
    """活动 ROI 高频读取直接结构化输出。"""
    from agent.workflows import business_metrics as metrics

    campaign_scope = metrics._required_data_scope("c")
    stats_scope = metrics._required_data_scope("cps")
    return _execute_rows(f"""
SELECT c.campaign_id, c.campaign_name, c.channel,
       SUM(cps.orders_count) AS orders_count,
       ROUND(SUM(cps.revenue), 2) AS revenue,
       ROUND(SUM(cps.spend), 2) AS spend,
       ROUND(SUM(cps.revenue) / NULLIF(SUM(cps.spend), 0), 2) AS roi,
       ROUND(SUM(cps.orders_count) / NULLIF(SUM(cps.clicks), 0), 4) AS click_to_order_rate
FROM campaigns c
JOIN campaign_product_stats cps ON cps.campaign_id = c.campaign_id AND {stats_scope}
WHERE {campaign_scope}
GROUP BY c.campaign_id, c.campaign_name, c.channel
ORDER BY roi DESC, revenue DESC
LIMIT {limit}
""")


def _json_value(value: Any) -> Any:
    """把 MySQL 返回的 Decimal/datetime 转成 JSON 友好的基础类型。"""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return value


def _parse_rows(raw_text: str) -> list[dict[str, Any]]:
    """把底层 CSV 文本解析成结构化 rows。"""
    if not raw_text:
        return []
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    header_index = next((index for index, line in enumerate(lines) if "," in line), -1)
    if header_index < 0:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_index:])))
    return [{key: _coerce_value(value) for key, value in row.items()} for row in reader]


def _post_process_rows(step: PlanStep, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对复用查询做 step 级结构化裁剪。"""
    if step.name == "query_low_conversion_products":
        sorted_rows = sorted(rows, key=lambda row: float(row.get("conversion_rate") or 0))
        return sorted_rows[: int(step.params.get("limit") or 8)]
    return rows


def _summarize_step(step_name: str, rows: list[dict[str, Any]]) -> str:
    """给 step 生成短摘要，供 trace、Reducer 和前端展示。"""
    if step_name == "query_inventory_risks":
        risky = [row for row in rows if str(row.get("risk_type") or "") != "watch"]
        return f"{len(risky)} 个 SKU 存在库存风险" if risky else "暂无明显库存风险"
    if step_name == "query_inventory_velocity":
        replenishment = [row for row in rows if float(row.get("suggested_replenishment") or 0) > 0]
        return f"{len(replenishment)} 个 SKU 建议补货"
    if step_name == "query_campaign_roi":
        return f"读取 {len(rows)} 条活动 ROI 数据"
    if step_name == "query_campaign_traffic":
        return f"读取 {len(rows)} 条活动流量数据"
    if step_name == "query_campaign_risks":
        return f"读取 {len(rows)} 条活动风险数据"
    if step_name == "query_hot_products":
        return f"读取 {len(rows)} 个商品综合表现"
    if step_name == "query_low_conversion_products":
        return f"识别 {len(rows)} 个低转化候选商品"
    if step_name == "query_daily_metrics":
        return "经营核心指标读取完成"
    if step_name == "query_daily_risks":
        return f"读取 {len(rows)} 条经营风险"
    return f"读取 {len(rows)} 行结构化数据"


def _coerce_value(value: Any) -> Any:
    """把 CSV 文本单元格尽量转成 int/float，失败时保留字符串。"""
    if value is None:
        return ""
    text = str(value).strip()
    if text == "":
        return ""
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def _is_query_error(result: str) -> bool:
    """识别旧文本查询函数返回的错误消息。"""
    return any(marker in result for marker in ("查询出现异常", "查询被拒绝", "缺失数据库核心配置"))