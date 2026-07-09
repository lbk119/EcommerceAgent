from __future__ import annotations

import pytest

from agent.plan.planner import PlannerAgent


@pytest.fixture
def planner(monkeypatch: pytest.MonkeyPatch) -> PlannerAgent:
    monkeypatch.setenv("PLANNER_AGENT_DISABLE_LLM", "1")
    return PlannerAgent()


@pytest.mark.parametrize(
    ("query", "agent_id"),
    [
        ("最近七天商品分?", "product_analysis"),
        ("库存风险和补货计?", "inventory"),
        ("活动 ROI 怎么?", "campaign"),
        ("生成周报", "report"),
        ("数据导入失败怎么?", "data_quality"),
    ],
)
def test_planner_dispatches_single_business_agent(planner: PlannerAgent, query: str, agent_id: str) -> None:
    plan = planner.plan(query, profile="standard")

    assert [assignment.agent_id for assignment in plan.assignments] == [agent_id]
    assert plan.boundary is False
    assert plan.requires_clarification is False


def test_planner_dispatches_multi_agent_business_question(planner: PlannerAgent) -> None:
    plan = planner.plan("商品 + 库存 + 活动风险", profile="standard")

    assert [assignment.agent_id for assignment in plan.assignments] == ["product_analysis", "inventory", "campaign"]
    assert plan.merge_strategy == "business_recommendation"


def test_planner_rewrites_assignment_tasks_and_keeps_tool_params_out(planner: PlannerAgent) -> None:
    raw_query = "帮我分析最近七天商品、库存和活动投放风险，给我一个优化建议?"
    plan = planner.plan(raw_query, profile="standard")

    for assignment in plan.assignments:
        assert assignment.task != raw_query
        assert assignment.original_query == raw_query
        assert assignment.objective
        assert assignment.expected_contribution
        assert assignment.assignment_scope
        assert "time_range" not in assignment.constraints
        assert "limit" not in assignment.constraints
        assert "category" not in assignment.constraints


def test_planner_generates_product_inventory_campaign_dependencies(planner: PlannerAgent) -> None:
    plan = planner.plan("帮我分析最近七天商品、库存和活动投放风险，给我一个优化建议?", profile="standard")
    by_agent = {assignment.agent_id: assignment.assignment_id for assignment in plan.assignments}
    edges = {(dependency.from_assignment, dependency.to_assignment, dependency.type) for dependency in plan.dependencies}

    assert (by_agent["product_analysis"], by_agent["inventory"], "blocking_data_dependency") in edges
    assert (by_agent["product_analysis"], by_agent["campaign"], "blocking_data_dependency") in edges
    assert (by_agent["inventory"], by_agent["campaign"], "optional_context") in edges
    assert by_agent["product_analysis"] in next(assignment for assignment in plan.assignments if assignment.agent_id == "inventory").depends_on


def test_planner_keeps_independent_views_parallel(planner: PlannerAgent) -> None:
    plan = planner.plan("帮我分别看一下最近七天商品表现和店铺日报?", profile="standard")

    assert {assignment.agent_id for assignment in plan.assignments} >= {"product_analysis", "report"}
    assert plan.dependencies == []
    assert all(assignment.depends_on == [] for assignment in plan.assignments)


def test_planner_boundary_for_out_of_scope_question(planner: PlannerAgent) -> None:
    plan = planner.plan("今天的天气怎么?", profile="standard")

    assert plan.boundary is True
    assert plan.primary_task_type == "boundary"


def test_planner_clarifies_vague_campaign(planner: PlannerAgent) -> None:
    plan = planner.plan("这个活动怎么?", profile="standard")

    assert plan.requires_clarification is True
    assert plan.primary_task_type == "campaign_review"
    assert "campaign_id_or_name" in plan.missing_context

