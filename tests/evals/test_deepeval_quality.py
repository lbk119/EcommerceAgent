from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from agent.plan.planner import PlannerAgent
from agent.security.prompt_guard import inspect_user_prompt


DATASET = Path(__file__).parent / "datasets" / "ecommerce_agent_cases.yaml"
REPORT_DIR = Path("output/nonfunctional")


def load_cases() -> list[dict]:
    return yaml.safe_load(DATASET.read_text(encoding="utf-8"))["cases"]


def score_plan(case: dict, plan) -> dict:
    expected_intent = case.get("expected_intent")
    expected_control = case.get("expected_control")
    guard = inspect_user_prompt(case["task"])
    assigned_agents = {assignment.agent_id for assignment in plan.assignments}
    assignment_intents = set(plan.assignment_intents)
    checks = {
        "planning_compliance": bool(plan.assignments or plan.execution_mode in {"boundary", "data_agent", "realtime_chat"}),
        "role_boundary": plan.execution_mode in {"boundary", "realtime_chat", "business_agent", "agent_orchestration", "data_agent"},
        "groundedness": bool(plan.expected_output),
        "actionability": bool(plan.expected_output),
        "hallucination_risk": assigned_agents <= {"product_analysis", "inventory", "campaign", "report", "data_quality", "knowledge_base", "network_search", "database_query"},
    }
    if expected_intent:
        checks["intent_match"] = expected_intent in assignment_intents or plan.primary_intent == expected_intent
        checks["primary_intent_reasonable"] = plan.primary_intent in assignment_intents or (plan.requires_clarification and plan.primary_intent == expected_intent) or plan.primary_task_type == "boundary"
    if expected_control == "boundary":
        checks["boundary"] = plan.execution_mode == "boundary"
    if expected_control == "clarification":
        checks["clarification"] = plan.requires_clarification
    if expected_control == "prompt_risk":
        checks["prompt_risk"] = guard.risk == "high"
    return {
        "id": safe_text(case["id"]),
        "category": category_for_case(case),
        "passed": all(checks.values()),
        "checks": checks,
        "expected_intent": safe_text(expected_intent or ""),
        "expected_control": safe_text(expected_control or ""),
        "actual_intent": safe_text(plan.primary_intent or plan.primary_task_type),
        "assignment_intents": [safe_text(intent) for intent in plan.assignment_intents],
        "execution_mode": safe_text(plan.execution_mode),
        "requires_clarification": bool(plan.requires_clarification),
        "assignment_count": len(plan.assignments),
        "failed_checks": [name for name, passed in checks.items() if not passed],
    }


@pytest.mark.evals
def test_mock_deepeval_quality_report(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANNER_AGENT_DISABLE_LLM", "1")
    cases = load_cases()
    planner = PlannerAgent()
    results = [score_plan(case, planner.plan(case["task"], profile="standard")) for case in cases]
    pass_rate = sum(1 for item in results if item["passed"]) / len(results)
    hallucination_checks = [item["checks"].get("hallucination_risk", True) for item in results]
    hallucination_pass_rate = sum(1 for item in hallucination_checks if item) / len(hallucination_checks)
    by_category = summarize_by_category(results)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = safe_json_payload({
        "status": "pass" if pass_rate >= 0.9 else "warn" if pass_rate >= 0.75 else "fail",
        "pass_rate": pass_rate,
        "hallucination_pass_rate": hallucination_pass_rate,
        "hallucination_passed": sum(1 for item in hallucination_checks if item),
        "hallucination_failed": sum(1 for item in hallucination_checks if not item),
        "passed": sum(1 for item in results if item["passed"]),
        "failed": sum(1 for item in results if not item["passed"]),
        "skipped": 0,
        "by_category": by_category,
        "results": results,
        "key_findings": [f"Planner mock eval pass rate: {pass_rate:.1%}"],
        "blocking_issues": [],
    })
    (REPORT_DIR / "eval-quality.json").write_text(json.dumps(report, ensure_ascii=True, indent=2, allow_nan=False), encoding="utf-8")

    assert len(cases) >= 30
    assert pass_rate >= 0.75


@pytest.mark.evals
def test_live_llm_evals_require_explicit_opt_in() -> None:
    if os.getenv("RUN_LIVE_LLM_EVALS") != "1":
        pytest.skip("set RUN_LIVE_LLM_EVALS=1 to enable DeepEval/Giskard live model judging")


def test_eval_dataset_is_valid_utf8_yaml() -> None:
    cases = load_cases()

    assert len(cases) >= 30
    assert all(isinstance(case.get("task"), str) and case["task"].strip() for case in cases)
    assert all(case.get("expected_intent") or case.get("expected_control") for case in cases)


def category_for_case(case: dict) -> str:
    explicit = case.get("category")
    if explicit:
        return safe_text(explicit)
    prefix = str(case.get("id", "general")).split("_", 1)[0]
    return {
        "hot": "hot_product",
        "product": "product_optimization",
        "inventory": "inventory",
        "campaign": "campaign",
        "report": "report",
        "seasonal": "seasonal",
        "data": "data_quality",
        "clarify": "clarification",
        "boundary": "boundary",
        "injection": "security",
    }.get(prefix, prefix or "general")


def summarize_by_category(results: list[dict]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for result in results:
        category = result["category"]
        item = summary.setdefault(category, {"total": 0, "passed": 0, "failed": 0, "pass_rate": 0.0, "failed_cases": []})
        item["total"] += 1
        if result["passed"]:
            item["passed"] += 1
        else:
            item["failed"] += 1
            item["failed_cases"].append({"id": result["id"], "failed_checks": result["failed_checks"]})
    for item in summary.values():
        item["pass_rate"] = item["passed"] / item["total"] if item["total"] else 0.0
    return summary


def safe_json_payload(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=True, allow_nan=False, default=str))


def safe_text(value: Any) -> str:
    text = str(value or "")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
