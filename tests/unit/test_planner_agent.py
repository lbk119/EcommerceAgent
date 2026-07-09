from __future__ import annotations

import pytest

from agent.plan.planner import PlannerAgent
from api.routes.ai_chat import _runtime_profile_for_chat


@pytest.fixture
def planner(monkeypatch: pytest.MonkeyPatch) -> PlannerAgent:
    monkeypatch.setenv("PLANNER_AGENT_DISABLE_LLM", "1")
    return PlannerAgent()


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("topproduct recommendation", "hot_product_analysis"),
        ("商品优化 and conversion", "product_optimization"),
        ("which product should I optimize", "product_optimization"),
        ("这个季节适合卖什?", "seasonal_selection"),
    ],
)
def test_planner_fallback_classifies_ecommerce_intents(planner: PlannerAgent, query: str, expected: str) -> None:
    plan = planner.plan(query, profile="standard")

    assert plan.primary_task_type == expected
    assert plan.primary_task_type == expected
    assert plan.execution_mode in {"business_agent", "agent_orchestration"}
    assert plan.assignments
    assert all(assignment.agent_id for assignment in plan.assignments)


def test_realtime_profile_keeps_unsupported_business_work_out_of_chat_path(planner: PlannerAgent) -> None:
    plan = planner.plan("数据导入失败怎么?", profile="realtime")

    assert plan.profile == "realtime"
    assert plan.execution_mode == "boundary"
    assert plan.fallback_reason == "realtime_profile_not_supported"
    assert _runtime_profile_for_chat({}, plan) == "standard"


def test_realtime_plain_chat_has_no_business_assignment(planner: PlannerAgent) -> None:
    plan = planner.plan("hello", profile="realtime")

    assert plan.profile == "realtime"
    assert plan.primary_task_type == "realtime_chat"
    assert plan.assignments == []
    assert plan.execution_mode == "realtime_chat"


def test_realtime_seasonal_product_selection_classifies_without_deep_runtime(planner: PlannerAgent) -> None:
    plan = planner.plan("seasonal product selection", profile="realtime")

    assert plan.primary_task_type == "seasonal_selection"
    assert plan.execution_mode != "deep_runtime"


def test_out_of_scope_request_returns_boundary(planner: PlannerAgent) -> None:
    plan = planner.plan("今天的天气怎么?", profile="standard")

    assert plan.execution_mode == "boundary"
    assert plan.primary_task_type == "boundary"
    assert plan.business_domain == "outside_ecommerce_operations"


def test_vague_campaign_request_requires_clarification(planner: PlannerAgent) -> None:
    plan = planner.plan("这个活动怎么?", profile="standard")

    assert plan.requires_clarification is True
    assert plan.execution_mode == "boundary"
    assert "campaign_id_or_name" in plan.missing_context
    assert plan.clarification_questions


def test_standard_data_quality_request_routes_to_data_agent(planner: PlannerAgent) -> None:
    plan = planner.plan("数据导入失败怎么?", profile="standard")

    assert plan.primary_task_type == "data_quality_check"
    assert plan.execution_mode == "data_agent"
    assert plan.assignments[0].agent_id == "data_quality"


@pytest.mark.asyncio
async def test_plan_async_uses_rule_based_fallback_when_llm_disabled(planner: PlannerAgent) -> None:
    plan = await planner.plan_async("topproduct recommendation", profile="standard")

    assert plan.primary_task_type == "hot_product_analysis"
    assert plan.fallback_reason.startswith("planner_model_failed")


def test_agent_assignments_have_valid_local_dependencies(planner: PlannerAgent) -> None:
    plan = planner.plan("爆品和库存风?", profile="standard")
    assignment_ids = {assignment.assignment_id for assignment in plan.assignments}

    assert assignment_ids
    assert all(dependency in assignment_ids for assignment in plan.assignments for dependency in assignment.depends_on)
    assert all(edge.from_assignment in assignment_ids and edge.to_assignment in assignment_ids for edge in plan.dependencies)
