from __future__ import annotations

from pathlib import Path

import pytest

from agent.evaluation.evaluation_agent import EvaluationIssue, EvaluationResult
from agent.memory import MemoryIdentity
from agent.runtime import result_pipeline
from agent.runtime.task_context import TaskRunContext


def context() -> TaskRunContext:
    return TaskRunContext(
        query="optimize products",
        conversation_id="conv",
        task_id="task",
        identity=MemoryIdentity(tenant_id="tenant", user_id="user", shop_id="shop"),
        session_dir=Path("output/session_conv"),
        session_dir_str="output/session_conv",
        relative_session_dir_str="output/session_conv",
        path_instruction="",
        config={"recursion_limit": 10},
        session_dir_token=None,
        thread_token=None,
        identity_token=None,
    )


@pytest.mark.asyncio
async def test_evaluation_failure_appends_quality_note_without_blocking_result(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_evaluation(*args, **kwargs):
        return EvaluationResult(
            passed=False,
            issues=[EvaluationIssue(type="missing_evidence", message="no source metrics")],
            fix_instruction="add metric evidence",
        )

    monkeypatch.setenv("EVALUATION_ENABLED", "true")
    monkeypatch.setattr(result_pipeline, "evaluate_evaluation_policy", lambda *args, **kwargs: type("Decision", (), {"required": True, "to_metadata": lambda self: {"required": True}})())
    monkeypatch.setattr(result_pipeline, "run_evaluation", fake_run_evaluation)
    monkeypatch.setattr(result_pipeline, "get_tool_calls_for_task", lambda task_id: [])
    monkeypatch.setattr(result_pipeline.monitor, "report_task_result", lambda result: None)
    monkeypatch.setattr(result_pipeline.tracer, "emit", lambda *args, **kwargs: None)

    result = await result_pipeline.run_evaluation_stage(context(), "base result", agent_specs=[], rerun_with_fix=None)

    assert result.evaluation_status == "failed"
    assert result.content.startswith("base result")
    assert "missing_evidence" in result.content


def test_standard_profile_defers_reflection_and_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(result_pipeline, "append_task_event", lambda event, task_id, payload: events.append((event, task_id, payload)))
    monkeypatch.setattr(result_pipeline, "append_reflection", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("reflection should be deferred")))
    monkeypatch.setattr(result_pipeline, "create_policy_proposal", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("policy proposal should be deferred")))
    monkeypatch.setattr(result_pipeline.tracer, "emit", lambda *args, **kwargs: None)

    reflection = result_pipeline.persist_result(context(), "result with password=secret", runtime_profile="standard")

    assert reflection["status"] == "deferred"
    assert events[0][0] == "task_completed"
    assert "[REDACTED]" in events[0][2]["result"]
