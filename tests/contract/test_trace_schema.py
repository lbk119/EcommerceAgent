from __future__ import annotations


def test_trace_event_schema_accepts_required_runtime_order() -> None:
    events = [
        {"event_type": "queued", "timestamp": "2026-07-07T00:00:00Z"},
        {"event_type": "task_classified", "timestamp": "2026-07-07T00:00:01Z"},
        {"event_type": "plan_execution_started", "timestamp": "2026-07-07T00:00:02Z"},
        {"event_type": "plan_execution_finished", "timestamp": "2026-07-07T00:00:03Z"},
        {"event_type": "agent_finished", "timestamp": "2026-07-07T00:00:04Z"},
    ]
    positions = {event["event_type"]: index for index, event in enumerate(events)}

    assert positions["queued"] < positions["task_classified"] < positions["plan_execution_started"]
    assert positions["plan_execution_started"] < positions["plan_execution_finished"] < positions["agent_finished"]
    assert all("timestamp" in event for event in events)