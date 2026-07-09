from __future__ import annotations

from agent.plan.models import AgentTaskPlan
from agent.plan.planner import PlannerAgent


def test_agent_taskplan_round_trips_trace_contract(monkeypatch) -> None:
    monkeypatch.setenv("PLANNER_AGENT_DISABLE_LLM", "1")
    plan = PlannerAgent().plan("topproduct recommendation", profile="standard")

    restored = AgentTaskPlan.from_dict(plan.to_dict())
    metadata = restored.to_trace_metadata()

    assert restored.to_dict() == plan.to_dict()
    assert metadata["task_plan"]["plan_id"] == plan.plan_id
    assert metadata["agent_assignment_count"] == len(plan.assignments)
    assert metadata["profile"] == "standard"
