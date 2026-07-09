from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from agent.trace import reader as trace_reader


def test_build_slow_tasks_reads_bounded_recent_trace_tail(tmp_path, monkeypatch):
    trace_path = tmp_path / "agent_traces.jsonl"
    started_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
    events = [
        {"task_id": "old-task", "timestamp": started_at.isoformat(), "event_type": "task.started"},
        {"task_id": "old-task", "timestamp": (started_at + timedelta(seconds=30)).isoformat(), "event_type": "task.done"},
        {"task_id": "recent-task", "timestamp": (started_at + timedelta(minutes=1)).isoformat(), "event_type": "task.started"},
        {"task_id": "recent-task", "timestamp": (started_at + timedelta(minutes=1, seconds=20)).isoformat(), "event_type": "task.done", "latency_ms": 20000},
    ]
    trace_path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    monkeypatch.setattr(trace_reader.tracer, "path", trace_path)

    result = trace_reader.build_slow_tasks(limit=5, max_events=2, max_scan_seconds=1.0)

    assert [task["task_id"] for task in result["tasks"]] == ["recent-task"]
    assert result["diagnostic"]["source"] == "trace_tail"
    assert result["diagnostic"]["scanned_events"] == 2
