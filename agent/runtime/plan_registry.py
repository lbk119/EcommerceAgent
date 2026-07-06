"""电商任务规则 PlanRegistry。

Planner 不是大模型。常见经营任务的执行计划在这里用规则表一次性生成，计划由多个互不依赖的数据
step 构成，后续交给 ParallelExecutor 并行执行。只有这里找不到计划的未知复杂任务，才允许 DeepAgent
作为 fallback 介入。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.workflows.business_metrics import BusinessTimeRange, parse_business_time_range


@dataclass(frozen=True)
class PlanStep:
    """计划中的单个执行 step。"""

    name: str
    label: str
    critical: bool = False
    timeout_seconds: float | None = None
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "critical": self.critical,
            "timeout_seconds": self.timeout_seconds,
            "params": self.params,
        }


@dataclass(frozen=True)
class ExecutionPlan:
    """一次任务的完整固定 DAG 计划。"""

    task_type: str
    workflow_name: str
    steps: tuple[PlanStep, ...]
    time_range: BusinessTimeRange
    output_requirements: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "workflow_name": self.workflow_name,
            "steps": [step.to_dict() for step in self.steps],
            "time_range": self.time_range.to_metadata(),
            "output_requirements": self.output_requirements,
            "metadata": self.metadata,
        }


PLAN_REGISTRY: dict[str, tuple[PlanStep, ...]] = {
    "inventory_analysis": (
        PlanStep("query_inventory_risks", "库存风险", critical=True),
        PlanStep("query_inventory_velocity", "销量与补货速度", critical=True),
        PlanStep("query_sales_trend", "销量趋势", critical=False),
    ),
    "inventory_warning": (
        PlanStep("query_inventory_risks", "库存风险", critical=True),
        PlanStep("query_inventory_velocity", "销量与补货速度", critical=True),
        PlanStep("query_sales_trend", "销量趋势", critical=False),
    ),
    "campaign_review": (
        PlanStep("query_campaign_traffic", "活动流量", critical=True),
        PlanStep("query_campaign_roi", "活动成交与 ROI", critical=True),
        PlanStep("query_campaign_risks", "活动风险", critical=False),
    ),
    "product_optimization": (
        PlanStep("query_hot_products", "商品综合表现", critical=True, params={"limit": 8}),
        PlanStep("query_low_conversion_products", "低转化商品", critical=False, params={"limit": 8}),
        PlanStep("query_inventory_velocity", "库存承接能力", critical=False),
    ),
    "daily_report": (
        PlanStep("query_daily_metrics", "经营核心指标", critical=True),
        PlanStep("query_daily_risks", "经营风险", critical=False),
        PlanStep("query_hot_products", "重点商品", critical=False, params={"limit": 6}),
        PlanStep("query_campaign_roi", "活动 ROI", critical=False),
    ),
    "hot_product_analysis": (
        PlanStep("query_hot_products", "爆品候选", critical=True, params={"limit": 8}),
        PlanStep("query_campaign_roi", "活动承接", critical=False),
        PlanStep("query_inventory_velocity", "库存承接", critical=False),
    ),
    "seasonal_selection": (
        PlanStep("query_shop_profile", "店铺画像", critical=False),
        PlanStep("query_hot_products", "商品销售表现", critical=True, params={"limit": 8}),
        PlanStep("query_inventory_velocity", "库存承接能力", critical=False),
        PlanStep("query_campaign_roi", "活动承接表现", critical=False),
    ),
}


OUTPUT_REQUIREMENTS: dict[str, str] = {
    "inventory_analysis": "输出库存分析，必须包含优先处理 SKU、低于安全库存原因、销量速度、补货动作和缺失数据。",
    "inventory_warning": "输出库存预警报告，必须包含风险 SKU、库存/安全库存、优先级和明确动作建议。",
    "campaign_review": "输出活动复盘，必须覆盖流量、成交、ROI、退款或库存风险，并给出下一轮优化动作。",
    "product_optimization": "输出商品优化建议，必须指出优先商品、原因、标题/价格/库存/活动建议和下一步动作。",
    "daily_report": "输出经营日报，必须包含核心指标、风险点、原因判断和下一步运营动作。",
    "hot_product_analysis": "输出爆品分析结论，必须覆盖销售、流量、转化、价格、库存、活动 ROI、退款风险和放量建议。",
    "seasonal_selection": "输出季节性选品建议，必须回应适合卖什么、当前店铺可承接商品、风险与下一步动作。",
}


class PlanRegistry:
    """规则 Planner。"""

    def plan(self, task_type: str, query: str, *, workflow_name: str = "") -> ExecutionPlan | None:
        """按 task_type/workflow_name 一次性生成固定 DAG。

        这里故意不调用 LLM，也不根据 step 结果动态追加 step；计划生成之后就交给并行执行器，避免回到
        “主 Agent 想一步、等一步、再想一步”的串行模式。
        """
        key = workflow_name or task_type
        steps = PLAN_REGISTRY.get(key) or PLAN_REGISTRY.get(task_type)
        if not steps:
            return None
        time_range = parse_business_time_range(query)
        resolved_task_type = task_type if task_type in PLAN_REGISTRY else key
        return ExecutionPlan(
            task_type=resolved_task_type,
            workflow_name=key,
            steps=steps,
            time_range=time_range,
            output_requirements=OUTPUT_REQUIREMENTS.get(key) or OUTPUT_REQUIREMENTS.get(resolved_task_type, "输出结构化经营分析结论。"),
            metadata={"planner": "rule_registry", "plan_version": "2026-07-06"},
        )