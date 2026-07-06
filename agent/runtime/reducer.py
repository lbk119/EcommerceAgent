"""计划执行结果 Reducer。

Reducer 先根据结构化 step JSON 生成确定性结论；fast model 只做表达润色。这样模型慢、超时或不可用时，
用户仍能收到基于真实数据的可读结果，而不会卡在“正在分析”。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from agent.observability.tracer import tracer
from agent.runtime.parallel_executor import PlanRunResult, StepResult
from agent.runtime.task_profiles import TaskExecutionProfile


@dataclass(frozen=True)
class ReducedResult:
    """Reducer 的结构化输出。"""

    content: str
    structured: dict[str, Any]
    deterministic_content: str
    polished: bool


class Reducer:
    """把并行 step 结果统一汇总成答案。"""

    def __init__(self, profile: TaskExecutionProfile):
        self.profile = profile

    async def reduce(self, run: PlanRunResult, *, query: str, trace_id: str, task_id: str, conversation_id: str, message_id: str = "") -> ReducedResult:
        started_at = time.perf_counter()
        tracer.emit("reducer_started", trace_id=trace_id, task_id=task_id, conversation_id=conversation_id, agent_name="reducer", metadata={"workflow_name": run.plan.workflow_name, "profile": self.profile.name})
        structured = self._build_structured_result(run)
        deterministic_content = _render_markdown(structured)
        if message_id:
            from api.monitor import monitor

            monitor.emit_assistant_delta(task_id=task_id, conversation_id=conversation_id, message_id=message_id, delta=deterministic_content)
        polished_content = await self._polish(query, run, structured, deterministic_content, trace_id=trace_id, task_id=task_id, conversation_id=conversation_id)
        latency_ms = round((time.perf_counter() - started_at) * 1000, 2)
        tracer.emit("reducer_finished", trace_id=trace_id, task_id=task_id, conversation_id=conversation_id, agent_name="reducer", latency_ms=latency_ms, metadata={"workflow_name": run.plan.workflow_name, "profile": self.profile.name, "polished": polished_content != deterministic_content})
        return ReducedResult(content=polished_content, structured=structured, deterministic_content=deterministic_content, polished=polished_content != deterministic_content)

    def _build_structured_result(self, run: PlanRunResult) -> dict[str, Any]:
        """生成不依赖模型的 conclusion/evidence/actions/risks/missingData。"""
        ok_results = {result.step: result for result in run.step_results if result.status == "ok"}
        missing_data = [item for result in run.step_results if result.status != "ok" for item in (result.missingData or [result.label])]
        workflow = run.plan.workflow_name
        if workflow in {"inventory_analysis", "inventory_warning"}:
            conclusion, evidence, actions, risks = _inventory_result(ok_results)
        elif workflow == "campaign_review":
            conclusion, evidence, actions, risks = _campaign_result(ok_results)
        elif workflow == "daily_report":
            conclusion, evidence, actions, risks = _daily_report_result(ok_results)
        elif workflow in {"product_optimization", "hot_product_analysis", "seasonal_selection"}:
            conclusion, evidence, actions, risks = _product_result(ok_results, workflow)
        else:
            conclusion, evidence, actions, risks = _generic_result(ok_results)
        if run.has_critical_failure:
            risks.insert(0, "关键数据节点失败，当前结论为降级结果。")
        return {
            "workflow": workflow,
            "profile": self.profile.name,
            "conclusion": conclusion,
            "evidence": evidence[:8],
            "actions": actions[:8],
            "risks": risks[:8],
            "missingData": missing_data,
            "stepSummaries": [result.to_dict() for result in run.step_results],
            "latencyMs": run.latencyMs,
            "timedOut": run.timedOut,
        }

    async def _polish(self, query: str, run: PlanRunResult, structured: dict[str, Any], deterministic_content: str, *, trace_id: str, task_id: str, conversation_id: str) -> str:
        """用 fast model 做可选润色；任何异常都直接回退确定性结果。"""
        if not self.profile.enable_fast_polish:
            return deterministic_content
        from agent.llm import get_fast_model

        prompt = f"""
你是电商经营分析助手。请只基于下面 JSON 润色表达，禁止新增 JSON 中不存在的数据。

用户问题：
{query}

结构化结果 JSON：
{json.dumps(structured, ensure_ascii=False)}

要求：
- 保留 conclusion、evidence、actions、risks、missingData 的事实。
- 输出中文 Markdown。
- 如果 missingData 不为空，要说明哪些数据缺失。
""".strip()
        started_at = time.perf_counter()
        tracer.emit("reducer_polish_started", trace_id=trace_id, task_id=task_id, conversation_id=conversation_id, agent_name="reducer", metadata={"workflow_name": run.plan.workflow_name, "timeout_seconds": self.profile.polish_timeout_seconds, "prompt_chars": len(prompt)})
        try:
            response = await asyncio.wait_for(get_fast_model().ainvoke(prompt), timeout=self.profile.polish_timeout_seconds)
            content = response.content if hasattr(response, "content") else str(response)
            tracer.emit("reducer_polish_finished", trace_id=trace_id, task_id=task_id, conversation_id=conversation_id, agent_name="reducer", latency_ms=round((time.perf_counter() - started_at) * 1000, 2), metadata={"workflow_name": run.plan.workflow_name})
            return content or deterministic_content
        except Exception as error:
            tracer.emit("reducer_polish_failed", trace_id=trace_id, task_id=task_id, conversation_id=conversation_id, agent_name="reducer", latency_ms=round((time.perf_counter() - started_at) * 1000, 2), error=str(error)[:1000], metadata={"workflow_name": run.plan.workflow_name, "fallback": "deterministic"})
            return f"{deterministic_content}\n\n> fast model 润色未完成，已先返回确定性结论。"


def _inventory_result(results: dict[str, StepResult]) -> tuple[str, list[str], list[str], list[str]]:
    risks_rows = results.get("query_inventory_risks", StepResult("", "", "missing", True)).rows
    velocity_rows = results.get("query_inventory_velocity", StepResult("", "", "missing", False)).rows
    risk_count = len([row for row in risks_rows if str(row.get("risk_type") or "") != "watch"])
    conclusion = f"当前优先处理 {risk_count} 个库存风险 SKU。" if risk_count else "当前没有明显高优先级库存风险。"
    evidence = [_product_line(row, prefix="库存风险") for row in risks_rows[:5]] + [_product_line(row, prefix="补货速度") for row in velocity_rows[:3]]
    actions = []
    for row in velocity_rows[:5]:
        replenish = float(row.get("suggested_replenishment") or 0)
        if replenish > 0:
            actions.append(f"{_product_name(row)} 建议补货 {int(replenish)} 件，并复核近 30 天销量。")
    if not actions:
        actions.append("维持日常库存监控，重点跟踪低于安全库存和近 30 天仍有销量的商品。")
    risks = [f"{_product_name(row)} 当前风险类型为 {row.get('risk_type')}。" for row in risks_rows[:4]]
    return conclusion, evidence, actions, risks


def _campaign_result(results: dict[str, StepResult]) -> tuple[str, list[str], list[str], list[str]]:
    traffic = results.get("query_campaign_traffic", StepResult("", "", "missing", True)).rows
    roi_rows = results.get("query_campaign_roi", StepResult("", "", "missing", True)).rows
    risks_rows = results.get("query_campaign_risks", StepResult("", "", "missing", False)).rows
    best = roi_rows[0] if roi_rows else {}
    conclusion = f"活动 `{best.get('campaign_name', '当前活动')}` ROI 为 {best.get('roi', 0)}，应优先复盘流量承接和风险商品。" if best else "当前活动数据不足，先返回降级复盘。"
    evidence = [f"{row.get('campaign_name')}：曝光 {row.get('impressions', 0)}，点击 {row.get('clicks', 0)}，CTR {row.get('ctr', 0)}。" for row in traffic[:3]]
    evidence += [f"{row.get('campaign_name')}：收入 {row.get('revenue', 0)}，花费 {row.get('spend', 0)}，ROI {row.get('roi', 0)}。" for row in roi_rows[:3]]
    actions = ["保留 ROI 靠前活动的商品组合，降低低点击或高风险商品的预算。", "对点击高但成交弱的活动，优先检查落地页、价格锚点和库存承接。"]
    risks = [f"{row.get('campaign_name')} / {_product_name(row)} 风险：{row.get('risk_type')}。" for row in risks_rows[:4]]
    return conclusion, evidence, actions, risks


def _daily_report_result(results: dict[str, StepResult]) -> tuple[str, list[str], list[str], list[str]]:
    metrics = results.get("query_daily_metrics", StepResult("", "", "missing", True)).rows
    risks_rows = results.get("query_daily_risks", StepResult("", "", "missing", False)).rows
    hot_rows = results.get("query_hot_products", StepResult("", "", "missing", False)).rows
    metric = metrics[0] if metrics else {}
    conclusion = f"经营期内 GMV {metric.get('gmv', 0)}，订单 {metric.get('orders_count', 0)}，退款率 {metric.get('refund_rate', 0)}。"
    evidence = [f"核心指标：销量 {metric.get('units_sold', 0)}，客单价 {metric.get('aov', 0)}，差评 {metric.get('bad_reviews', 0)}。"] if metric else []
    evidence += [_product_line(row, prefix="重点商品") for row in hot_rows[:4]]
    actions = ["先处理风险项，再围绕销售额靠前且库存可承接的商品做放量。", "日报结论应同步给商品、库存和活动负责人拆解动作。"]
    risks = [f"{row.get('risk_type')}={row.get('risk_value')}" for row in risks_rows[:5]]
    return conclusion, evidence, actions, risks


def _product_result(results: dict[str, StepResult], workflow: str) -> tuple[str, list[str], list[str], list[str]]:
    products = results.get("query_hot_products", StepResult("", "", "missing", True)).rows
    low_conversion = results.get("query_low_conversion_products", StepResult("", "", "missing", False)).rows
    inventory = results.get("query_inventory_velocity", StepResult("", "", "missing", False)).rows
    top = products[0] if products else {}
    if workflow == "seasonal_selection":
        conclusion = f"应季方向优先围绕 {_product_name(top) if top else '当前已验证商品'}，再结合库存和活动承接做组合。"
    elif workflow == "hot_product_analysis":
        conclusion = f"最值得放量的候选是 {_product_name(top)}，销售额 {top.get('sales_amount', 0)}，转化率 {top.get('conversion_rate', 0)}。" if top else "当前商品数据不足，先返回降级爆品建议。"
    else:
        conclusion = f"优先优化 {_product_name(top)}，同时处理低转化候选商品。" if top else "当前商品数据不足，先返回降级优化建议。"
    evidence = [_product_line(row, prefix="商品表现") for row in products[:5]]
    evidence += [_product_line(row, prefix="低转化") for row in low_conversion[:3]]
    actions = ["高销售且库存充足的商品优先放量；高访客低转化商品优先优化标题、主图、价格和评价承接。"]
    actions += [f"{_product_name(row)} 库存 {row.get('stock', 0)}，建议动作：{row.get('suggested_action', '复核库存承接')}。" for row in inventory[:3]]
    risks = [f"{_product_name(row)} 退款 {row.get('refunds_count', 0)}，库存 {row.get('stock', 0)}。" for row in products[:4]]
    return conclusion, evidence, actions, risks


def _generic_result(results: dict[str, StepResult]) -> tuple[str, list[str], list[str], list[str]]:
    evidence = [result.summary for result in results.values() if result.summary]
    return "已完成固定 DAG 数据读取并生成降级结论。", evidence, ["请按 evidence 中的高风险项逐项处理。"], []


def _render_markdown(structured: dict[str, Any]) -> str:
    lines = ["## 结论", str(structured.get("conclusion") or "暂无结论。")]
    for title, key in (("证据", "evidence"), ("建议动作", "actions"), ("风险", "risks"), ("缺失数据", "missingData")):
        items = structured.get(key) or []
        lines.append(f"\n## {title}")
        if items:
            lines.extend([f"- {item}" for item in items])
        else:
            lines.append("- 暂无。")
    return "\n".join(lines)


def _product_line(row: dict[str, Any], *, prefix: str) -> str:
    return f"{prefix}：{_product_name(row)}，销售额 {row.get('sales_amount') or row.get('sales_amount_30d') or 0}，销量 {row.get('units_sold') or row.get('units_sold_30d') or 0}，库存 {row.get('stock', 0)}。"


def _product_name(row: dict[str, Any]) -> str:
    return str(row.get("product_name") or row.get("product_id") or row.get("category_name_en") or "商品")