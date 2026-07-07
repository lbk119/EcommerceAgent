"""PlannerAgent smoke tests.

This script is intentionally DB-free: it validates planning, routing, clarification, boundary, and LLM-failure
fallback behavior without executing workflow SQL.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent.planning.planner_agent import PlannerAgent


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def step_tasks(plan) -> set[str]:
    return {step.task_type for step in plan.steps}


def step_names(plan) -> set[str]:
    return {step.name for step in plan.steps}


async def main() -> None:
    planner = PlannerAgent()

    started = time.perf_counter()
    hot_plan = planner.plan("推荐我最近爆品", profile="realtime")
    accepted_latency_ms = (time.perf_counter() - started) * 1000
    assert_true(accepted_latency_ms < 1000, f"planner sync accept too slow: {accepted_latency_ms:.2f}ms")
    assert_true(hot_plan.execution_mode in {"deterministic_dag", "hybrid_plan"}, "hot product should use plan-first mode")
    assert_true("hot_product_analysis" in step_tasks(hot_plan), "hot product plan should include product expert capability")
    assert_true(hot_plan.execution_mode != "deepagent", "realtime hot product must not enter deepagent")

    diagnosis_plan = planner.plan("帮我看看店铺最近怎么样", profile="realtime")
    assert_true(diagnosis_plan.intent.primary_goal in {"daily_report", "general_business_chat"}, "vague store diagnosis should map to report/business plan")
    assert_true(len(diagnosis_plan.steps) >= 2, "store diagnosis should contain multi-step DAG")

    combo_plan = planner.plan("这个月哪些商品适合加大投放，同时帮我看库存和活动风险", profile="standard")
    names = step_names(combo_plan)
    assert_true("query_hot_products" in names, "combo plan should include product performance step")
    assert_true("query_inventory_velocity" in names or "query_inventory_risks" in names, "combo plan should include inventory step")
    assert_true("query_campaign_roi" in names or "query_campaign_risks" in names, "combo plan should include campaign step")
    assert_true(all(step.can_parallel for step in combo_plan.steps if not step.depends_on), "independent combo steps should be parallel-capable")

    weather_plan = planner.plan("今天天气怎么样", profile="realtime")
    assert_true(weather_plan.execution_mode == "boundary", "weather should be boundary")
    assert_true(not weather_plan.steps, "boundary plan must not execute business tools")

    clarification_plan = planner.plan("帮我分析一下那个活动", profile="realtime")
    assert_true(clarification_plan.requires_clarification, "vague campaign should require clarification")
    assert_true(clarification_plan.clarification_questions, "clarification plan should include questions")

    old_disable = os.environ.get("PLANNER_AGENT_DISABLE_LLM")
    os.environ["PLANNER_AGENT_DISABLE_LLM"] = "true"
    try:
        fallback_plan = await planner.plan_async("推荐我最近爆品", profile="realtime")
        assert_true(fallback_plan.execution_mode in {"deterministic_dag", "hybrid_plan"}, "LLM failure fallback should still plan common ecommerce task")
        assert_true("query_hot_products" in step_names(fallback_plan), "fallback hot plan should include deterministic hot product step")
    finally:
        if old_disable is None:
            os.environ.pop("PLANNER_AGENT_DISABLE_LLM", None)
        else:
            os.environ["PLANNER_AGENT_DISABLE_LLM"] = old_disable

    print("PlannerAgent smoke passed")


if __name__ == "__main__":
    asyncio.run(main())
