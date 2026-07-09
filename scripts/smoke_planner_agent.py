"""PlannerAgent smoke tests.

This script is intentionally DB-free: it validates planning, business-agent routing, clarification, boundary, and
LLM-failure fallback behavior without executing tool SQL.
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

from agent.plan.planner import PlannerAgent


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def assignment_intents(plan) -> set[str]:
    return {assignment.intent for assignment in plan.assignments}


def assignment_agents(plan) -> set[str]:
    return {assignment.agent_id for assignment in plan.assignments}


async def main() -> None:
    planner = PlannerAgent()

    started = time.perf_counter()
    hot_plan = planner.plan("推荐我最近爆?", profile="realtime")
    accepted_latency_ms = (time.perf_counter() - started) * 1000
    assert_true(accepted_latency_ms < 1000, f"planner sync accept too slow: {accepted_latency_ms:.2f}ms")
    assert_true("product_analysis" in assignment_agents(hot_plan), "hot product should route to ProductAnalysisAgent")
    assert_true("hot_product_analysis" in assignment_intents(hot_plan), "hot product plan should include product intent")
    assert_true(not hot_plan.boundary, "realtime hot product must stay inside business-agent orchestration")

    diagnosis_plan = planner.plan("帮我看看店铺最近怎么?", profile="realtime")
    assert_true(assignment_intents(diagnosis_plan) & {"daily_report", "general_business_chat"}, "vague store diagnosis should map to report/business plan")
    assert_true("report" in assignment_agents(diagnosis_plan), "store diagnosis should route to ReportAgent")

    combo_plan = planner.plan("这个月哪些商品适合加大投放，同时帮我看库存和活动风?", profile="standard")
    agents = assignment_agents(combo_plan)
    assert_true("product_analysis" in agents, "combo plan should include product analysis agent")
    assert_true("inventory" in agents, "combo plan should include inventory agent")
    assert_true("campaign" in agents, "combo plan should include campaign agent")
    assert_true(combo_plan.merge_strategy == "business_recommendation", "combo plan should use business recommendation merge")

    weather_plan = planner.plan("今天天气怎么?", profile="realtime")
    assert_true(weather_plan.boundary, "weather should be boundary")
    assert_true(not weather_plan.assignments, "boundary plan must not route business agents")

    clarification_plan = planner.plan("帮我分析一下那个活?", profile="realtime")
    assert_true(clarification_plan.requires_clarification, "vague campaign should require clarification")
    assert_true(clarification_plan.clarification_questions, "clarification plan should include questions")

    old_disable = os.environ.get("PLANNER_AGENT_DISABLE_LLM")
    os.environ["PLANNER_AGENT_DISABLE_LLM"] = "true"
    try:
        fallback_plan = await planner.plan_async("推荐我最近爆?", profile="realtime")
        assert_true("product_analysis" in assignment_agents(fallback_plan), "LLM failure fallback should still route common ecommerce task")
        assert_true("hot_product_analysis" in assignment_intents(fallback_plan), "fallback hot plan should preserve hot product intent")
    finally:
        if old_disable is None:
            os.environ.pop("PLANNER_AGENT_DISABLE_LLM", None)
        else:
            os.environ["PLANNER_AGENT_DISABLE_LLM"] = old_disable

    print("PlannerAgent smoke passed")


if __name__ == "__main__":
    asyncio.run(main())
